"""
东方财富 API 客户端 - 使用 Playwright 绕过 TLS 指纹检测
替代 AKShare 的 *_em() 系列函数

Playwright 同步 API 的对象只能在创建它的线程里使用，而采集任务分布在多个工作线程上，
所以浏览器由一个专用线程持有，所有请求都提交到该线程执行（调用方可在任意线程等待结果）。
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import pandas as pd
from loguru import logger
from playwright.sync_api import sync_playwright
from src.utils.redaction import redact_text

HTTP_OK_STATUS = 200
DEFAULT_REFERER = "https://quote.eastmoney.com/"
BROWSER_STARTUP_SECONDS = 30  # 首次请求要启动浏览器并预热主页


class EastMoneyClient:
    """东方财富 API 客户端（单例模式，复用浏览器实例）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="em-playwright")
        self._initialized = True

    def _ensure_browser(self):
        """确保浏览器已启动（只在浏览器专用线程调用）"""
        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context()
            # 预热：访问东方财富主页建立 session
            self._page = self._context.new_page()
            try:
                self._page.goto('https://quote.eastmoney.com/', timeout=15000)
                self._page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"东方财富主页预热失败: {redact_text(e, 500)}")

    def request_json(self, url: str, params: dict | None = None, timeout: int = 15000, referer: str | None = None) -> dict | None:
        """通用 JSON 请求（带浏览器 TLS 指纹），供各采集器调用东方财富的其他接口；失败返回 None。"""
        return self._request(url, params=params, timeout=timeout, referer=referer)

    def _request(self, url: str, params: dict = None, timeout: int = 15000, referer: str | None = None) -> dict | None:
        """发送 GET 请求（带浏览器 TLS 指纹）；可在任意线程调用，实际在浏览器专用线程执行。"""
        future = self._executor.submit(self._request_in_browser_thread, url, params, timeout, referer)
        try:
            return future.result(timeout=timeout / 1000 + BROWSER_STARTUP_SECONDS)
        except Exception as e:
            logger.warning(f"东方财富 API 请求失败: {redact_text(e, 500)}")
            return None

    def _request_in_browser_thread(self, url: str, params: dict | None, timeout: int, referer: str | None) -> dict | None:
        try:
            self._ensure_browser()
            resp = self._context.request.get(
                url,
                params=params or {},
                headers={'Referer': referer or DEFAULT_REFERER},
                timeout=timeout
            )
            if resp.status != HTTP_OK_STATUS:
                logger.warning(f"东方财富 API 返回 {resp.status}: {url}")
                return None
            return resp.json()
        except Exception as e:
            logger.warning(f"东方财富 API 请求失败: {redact_text(e, 500)}")
            return None

    def close(self):
        """关闭浏览器（在浏览器专用线程执行）"""
        if self._browser is None and self._playwright is None:
            return
        with suppress(Exception):
            self._executor.submit(self._close_in_browser_thread).result(timeout=15)

    def _close_in_browser_thread(self):
        if self._page:
            self._page.close()
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None

    def __del__(self):
        with suppress(Exception):
            self.close()

    # ========== AKShare 兼容接口 ==========

    def stock_zt_pool_em(self, date: str) -> pd.DataFrame | None:
        """涨停池（替代 ak.stock_zt_pool_em）"""
        data = self._request(
            "https://push2ex.eastmoney.com/getTopicZTPool",
            params={
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": "0",
                "pagesize": "10000",
                "sort": "fbt:asc",
                "date": date,
            }
        )
        if not data or 'data' not in data:
            return None

        items = data['data'].get('pool', []) if isinstance(data['data'], dict) else data['data']
        if not items:
            return pd.DataFrame()

        df = pd.DataFrame(items)
        rename_map = {
            'c': '代码', 'n': '名称', 'p': '最新价', 'zdp': '涨跌幅',
            'lbc': '连板数', 'fbt': '首次封板时间', 'lbt': '最后封板时间',
            'zbc': '炸板次数', 'hybk': '所属行业', 'fund': '封板资金',
            'ltsz': '流通市值',
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        # 价格单位转换（原始单位为分）
        if '最新价' in df.columns:
            df['最新价'] = df['最新价'] / 100
        # 封板时间格式化：92500 -> "09:25:00"
        for col in ['首次封板时间', '最后封板时间']:
            if col in df.columns:
                def fmt_time(v):
                    try:
                        s = str(int(v)).zfill(6)
                        return f"{s[:2]}:{s[2:4]}:{s[4:]}"
                    except Exception:
                        return str(v)
                df[col] = df[col].apply(fmt_time)
        return df

    def stock_zt_pool_strong_em(self, date: str) -> pd.DataFrame | None:
        """强势股池（替代 ak.stock_zt_pool_strong_em）"""
        data = self._request(
            "https://push2ex.eastmoney.com/getTopicQSPool",
            params={
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": "0",
                "pagesize": "5000",
                "sort": "zdp:desc",
                "date": date,
            }
        )
        if not data or 'data' not in data:
            return None

        items = data['data'].get('pool', []) if isinstance(data['data'], dict) else data['data']
        if not items:
            return pd.DataFrame()

        df = pd.DataFrame(items)
        rename_map = {
            'c': '代码', 'n': '名称', 'p': '最新价', 'zdp': '涨跌幅',
            'lbc': '连板数', 'hybk': '入选理由', 'tname': '所属行业',
            'ltsz': '流通市值',
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        if '最新价' in df.columns:
            df['最新价'] = df['最新价'] / 100
        return df

    def stock_lhb_detail_em(self, start_date: str, end_date: str) -> pd.DataFrame | None:
        """龙虎榜明细（替代 ak.stock_lhb_detail_em）"""
        data = self._request(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "sortColumns": "SECURITY_CODE,TRADE_DATE",
                "sortTypes": "1,-1",
                "pageSize": "5000",
                "pageNumber": "1",
                "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
                "columns": "SECURITY_CODE,SECUCODE,SECURITY_NAME_ABBR,TRADE_DATE,EXPLAIN,CLOSE_PRICE,CHANGE_RATE,"
                           "BILLBOARD_NET_AMT,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,BILLBOARD_DEAL_AMT,ACCUM_AMOUNT,"
                           "DEAL_NET_RATIO,DEAL_AMOUNT_RATIO,TURNOVERRATE,FREE_MARKET_CAP,EXPLANATION,D1_CLOSE_ADJCHRATE,"
                           "D2_CLOSE_ADJCHRATE,D5_CLOSE_ADJCHRATE,D10_CLOSE_ADJCHRATE,SECURITY_TYPE_CODE",
                "source": "WEB",
                "client": "WEB",
                "filter": f"(TRADE_DATE<='{end_date}')(TRADE_DATE>='{start_date}')",
            }
        )
        if not data or 'result' not in data:
            return None
        if data['result'] is None or 'data' not in data['result']:
            return None

        items = data['result']['data']
        if not items:
            return pd.DataFrame()

        df = pd.DataFrame(items)
        rename_map = {
            'SECURITY_CODE': '代码', 'SECURITY_NAME_ABBR': '名称',
            'EXPLAIN': '解读', 'BILLBOARD_BUY_AMT': '买入额',
            'BILLBOARD_SELL_AMT': '卖出额', 'BILLBOARD_NET_AMT': '净额',
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        return df

    def stock_hsgt_fund_flow_summary_em(self) -> pd.DataFrame | None:
        """北向资金流向汇总（替代 ak.stock_hsgt_fund_flow_summary_em）"""
        data = self._request(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "reportName": "RPT_MUTUAL_QUOTA",
                "columns": "TRADE_DATE,MUTUAL_TYPE,BOARD_TYPE,MUTUAL_TYPE_NAME,FUNDS_DIRECTION,"
                           "INDEX_CODE,INDEX_NAME,BOARD_CODE",
                "quoteColumns": "status~07~BOARD_CODE,dayNetAmtIn~07~BOARD_CODE,dayAmtRemain~07~BOARD_CODE,"
                                "dayAmtThreshold~07~BOARD_CODE,f104~07~BOARD_CODE,f105~07~BOARD_CODE,"
                                "f106~07~BOARD_CODE,f3~03~INDEX_CODE~INDEX_f3,netBuyAmt~07~BOARD_CODE",
                "quoteType": "0",
                "pageNumber": "1",
                "pageSize": "2000",
                "sortTypes": "1",
                "sortColumns": "MUTUAL_TYPE",
                "source": "WEB",
                "client": "WEB",
            }
        )
        if not data or 'result' not in data:
            return None
        if data['result'] is None or 'data' not in data['result']:
            return None

        items = data['result']['data']
        if not items:
            return pd.DataFrame()

        df = pd.DataFrame(items)
        rename_map = {
            'TRADE_DATE': '交易日', 'FUNDS_DIRECTION': '资金方向',
            'netBuyAmt': '成交净买额',
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        return df

    def stock_hsgt_hist_em(self, symbol: str) -> pd.DataFrame | None:
        """沪深港通历史数据（替代 ak.stock_hsgt_hist_em）"""
        symbol_map = {
            "北向资金": "1",
            "沪股通": "3",
            "深股通": "4",
        }
        if symbol not in symbol_map:
            logger.warning(f"不支持的 symbol: {symbol}")
            return None

        data = self._request(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "sortColumns": "TRADE_DATE",
                "sortTypes": "-1",
                "pageSize": "1000",
                "pageNumber": "1",
                "reportName": "RPT_MUTUAL_DEAL_HISTORY",
                "columns": "ALL",
                "source": "WEB",
                "client": "WEB",
                "filter": f'(MUTUAL_TYPE="00{symbol_map[symbol]}")',
            }
        )
        if not data or 'result' not in data:
            return None
        if data['result'] is None or 'data' not in data['result']:
            return None

        items = data['result']['data']
        if not items:
            return pd.DataFrame()

        df = pd.DataFrame(items)
        rename_map = {
            'TRADE_DATE': '日期', 'MUTUAL_TYPE_NAME': '类型',
            'NET_BUY_AMT': '当日净流入', 'ACCUM_NET_BUY_AMT': '净流入',
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        return df

    def stock_zh_a_spot_em(self) -> pd.DataFrame | None:
        """A股实时行情（替代 ak.stock_zh_a_spot_em）"""
        data = self._request(
            "https://82.push2.eastmoney.com/api/qt/clist/get",
            params={
                "pn": "1",
                "pz": "50000",  # 增大分页，一次拉完
                "po": "1",
                "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2",
                "invt": "2",
                "fid": "f12",
                "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
                "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,"
                          "f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152",
            },
            timeout=30000
        )
        if not data or 'data' not in data or 'diff' not in data['data']:
            return None

        items = data['data']['diff']
        if not items:
            return pd.DataFrame()

        df = pd.DataFrame(items)
        # f12=代码, f14=名称, f2=最新价, f3=涨跌幅, f15=最高, f16=最低, f17=今开
        # f5=成交量(手), f6=成交额, f8=换手率, f20=总市值, f21=流通市值, f9=市盈率(动态), f23=市净率
        rename_map = {
            'f12': '代码', 'f14': '名称', 'f2': '最新价', 'f3': '涨跌幅',
            'f15': '最高', 'f16': '最低', 'f17': '今开',
            'f5': '成交量', 'f6': '成交额', 'f8': '换手率',
            'f20': '总市值', 'f21': '流通市值', 'f9': '市盈率', 'f23': '市净率',
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        # 成交量单位转换（手 -> 股）
        if '成交量' in df.columns:
            df['成交量'] = df['成交量'] * 100
        return df

    def stock_zh_index_spot_em(self) -> pd.DataFrame | None:
        """指数实时行情（替代 ak.stock_zh_index_spot_em）"""
        data = self._request(
            "https://48.push2.eastmoney.com/api/qt/clist/get",
            params={
                "pn": "1",
                "pz": "100",
                "po": "1",
                "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2",
                "invt": "2",
                "wbp2u": "|0|0|0|web",
                "fid": "f12",
                "fs": "m:1 s:2",  # 上证/深证/创业板指数
                "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,"
                          "f26,f22,f33,f11,f62,f128,f136,f115,f152",
            }
        )
        if not data or 'data' not in data or 'diff' not in data['data']:
            return None

        items = data['data']['diff']
        if not items:
            return pd.DataFrame()

        df = pd.DataFrame(items)
        rename_map = {
            'f12': '代码', 'f14': '名称', 'f2': '最新价', 'f3': '涨跌幅',
            'f6': '成交额',
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        return df

    def stock_board_concept_name_em(self) -> pd.DataFrame | None:
        """概念板块名称（替代 ak.stock_board_concept_name_em）"""
        data = self._request(
            "https://79.push2.eastmoney.com/api/qt/clist/get",
            params={
                "pn": "1",
                "pz": "500",
                "po": "1",
                "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2",
                "invt": "2",
                "fid": "f12",
                "fs": "m:90 t:3 f:!50",
                "fields": "f2,f3,f4,f8,f12,f14,f15,f16,f17,f18,f20,f21,f24,f25,f22,f33,f11,f62,f128,f124,f107,f104,f105,f136",
            }
        )
        if not data or 'data' not in data or 'diff' not in data['data']:
            return None

        items = data['data']['diff']
        if not items:
            return pd.DataFrame()

        df = pd.DataFrame(items)
        rename_map = {
            'f12': '板块代码', 'f14': '板块名称', 'f3': '涨跌幅',
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        return df


# 全局单例
_em_client = None


def get_em_client() -> EastMoneyClient:
    """获取东方财富客户端单例"""
    global _em_client
    if _em_client is None:
        _em_client = EastMoneyClient()
    return _em_client
