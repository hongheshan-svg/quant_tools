"""
AKShare 行情数据采集器
覆盖：实时行情、涨停池、龙虎榜、北向资金、融资融券
"""

from contextlib import suppress
from datetime import date, datetime
from typing import Any

import akshare as ak
import pandas as pd
from loguru import logger

from src.collectors.base import BaseCollector
from src.collectors.fund_flow import collect_fund_flow
from src.collectors.limit_up_reasons import fetch_ths_limit_up_reasons
from src.collectors.source_chain import fetch_with_fallback, source_health
from src.utils.stock_code import board_of, daily_limit_pct, normalize_name
from src.collectors.em_client import get_em_client
from src.database.db import get_db_session
from src.database.models import (
    DragonTigerBoard,
    LimitUpStock,
    NorthboundFlow,
    StockDaily,
    StockInfo,
)

HTTP_OK_STATUS = 200
PREFIXED_STOCK_CODE_LENGTH = 8
STOCK_CODE_LENGTH = 6
MIN_MARKET_OVERVIEW_SAMPLE_SIZE = 100
LIMIT_UP_CHANGE_THRESHOLD = 9.8
LIMIT_DOWN_CHANGE_THRESHOLD = -9.8
EXTREME_LOW_LIQUIDITY_AMOUNT_YI = 15000
LOW_LIQUIDITY_AMOUNT_YI = 20000
EMOTION_MULTIPLIER_THRESHOLD = 2
BULL_MARKET_LIMIT_UP_MIN = 50
BULL_MARKET_LIMIT_DOWN_MAX = 30
PANIC_LIMIT_DOWN_MIN = 60
BIAS_MULTIPLIER_THRESHOLD = 1.5
SINA_INDEX_MAX_LINES = 3
SINA_MIN_PARTS = 4
NORTHBOUND_PARTS_MIN_LENGTH = 2
MIN_DB_CODE_COUNT = 2500
MIN_EM_CODE_COUNT = 1000
TENCENT_MIN_FIELDS = 45
TENCENT_TOTAL_MV_INDEX = 45
TENCENT_CIRC_MV_INDEX = 44
TENCENT_PE_INDEX = 39
TENCENT_PB_INDEX = 46
NORTHBOUND_UNIT_SPLIT_THRESHOLD = 10000
LIMIT_UP_PCT_TOLERANCE = 0.5  # 涨停价四舍五入导致实际涨幅略低于 10%/20%/30%
# 实时行情默认的数据源顺序（data_sources.realtime 可调整）
REALTIME_SOURCES = ("tencent", "eastmoney", "sina", "efinance", "pytdx")


class StockDataCollector(BaseCollector):
    """AKShare 行情数据采集器"""

    SOURCE_NAME = "akshare"

    # 内存缓存: 最近一次采集的市场概况
    _market_overview_cache: dict[str, Any] = {}

    @staticmethod
    def _extract_bare_equity_code(raw_code: str | None) -> str:
        """
        提取 A 股个股 6 位代码（过滤指数代码）。
        支持输入:
          - 000001
          - sz000001 / sh600000 / bj430047
        返回:
          - 合法个股: 6位数字
          - 非法或指数: ""
        """
        raw = (raw_code or "").strip().lower()
        if not raw:
            return ""

        if len(raw) == PREFIXED_STOCK_CODE_LENGTH and raw[:2] in {"sh", "sz", "bj"} and raw[2:].isdigit():
            bare = raw[2:]
            prefix = raw[:2]
            if prefix == "sh" and bare.startswith(("6", "9")):
                return bare
            if prefix == "sz" and bare.startswith(("0", "1", "2", "3")):
                return bare
            if prefix == "bj" and bare.startswith(("4", "8")):
                return bare
            return ""

        if len(raw) == STOCK_CODE_LENGTH and raw.isdigit() and raw.startswith(("0", "1", "2", "3", "4", "6", "8", "9")):
            return raw
        return ""

    @staticmethod
    def _to_tencent_code(raw_code: str | None) -> str:
        """
        归一化为腾讯行情查询代码:
          000001 -> sz000001
          600000 -> sh600000
          430047 -> bj430047
        """
        raw = (raw_code or "").strip().lower()
        if len(raw) == PREFIXED_STOCK_CODE_LENGTH and raw[:2] in {"sh", "sz", "bj"} and raw[2:].isdigit():
            if StockDataCollector._extract_bare_equity_code(raw):
                return raw
            return ""

        bare = StockDataCollector._extract_bare_equity_code(raw)
        if not bare:
            return ""
        if bare.startswith(("6", "9")):
            return f"sh{bare}"
        if bare.startswith(("4", "8")):
            return f"bj{bare}"
        return f"sz{bare}"

    @staticmethod
    def _dedupe_keep_order(items: list[str]) -> list[str]:
        seen = set()
        result = []
        for x in items:
            if x and x not in seen:
                seen.add(x)
                result.append(x)
        return result

    def collect(self) -> list[dict[str, Any]]:
        """采集全部行情数据（这些数据按今天的日期入库，节假日和开盘前不采集）"""
        from src import trading_calendar

        today = date.today().strftime("%Y-%m-%d")
        db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        if not trading_calendar.market_data_ready():
            logger.info("非交易日或未到 9:25，跳过行情采集（接口此时返回的是上一个交易日的数据）")
            return []

        self._collect_realtime_quotes(today, db_path)
        self._collect_limit_up_pool(today, db_path)
        self._collect_dragon_tiger(today, db_path)
        self._collect_northbound_flow(today, db_path)
        collect_fund_flow(today, db_path)

        return []  # 数据已直接写入数据库

    def collect_market_overview(self) -> dict[str, Any]:
        """
        采集市场全局概况：大盘成交额、涨跌家数、涨停跌停数、板块涨幅 TOP、市场情绪指标。
        优先从数据库已采集的 StockDaily 统计，减少重复网络请求。
        结果缓存在类变量中，UI / AI 可直接读取。
        """
        overview: dict[str, Any] = {
            "total_amount_yi": 0,       # 两市总成交额（亿）
            "up_count": 0,              # 上涨家数
            "down_count": 0,            # 下跌家数
            "flat_count": 0,            # 平盘家数
            "limit_up_count": 0,        # 涨停数
            "limit_down_count": 0,      # 跌停数
            "avg_change_pct": 0.0,      # 全市场平均涨幅
            "sh_index": "",             # 上证指数
            "sh_change_pct": 0.0,       # 上证涨跌幅
            "sz_index": "",             # 深证成指
            "sz_change_pct": 0.0,       # 深证涨跌幅
            "cy_index": "",             # 创业板指
            "cy_change_pct": 0.0,       # 创业板涨跌幅
            "top_sectors": [],          # 涨幅前5板块
            "bottom_sectors": [],       # 跌幅前5板块
            "northbound_net_yi": 0.0,   # 北向资金净流入（亿）
            "market_emotion": "",       # 市场情绪标签
            "update_time": "",
        }

        today = date.today().strftime("%Y-%m-%d")
        db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")

        # ======== 1. 优先从DB的StockDaily统计（已采集的行情数据） ========
        stat_date = today
        try:
            from src.database.db import get_db_session
            from src.database.models import StockDaily as SD
            with get_db_session(db_path) as session:
                all_rows = session.query(SD).filter(SD.trade_date == today).all()
                daily_all = [r for r in all_rows if self._extract_bare_equity_code(r.code)]
                if len(daily_all) <= MIN_MARKET_OVERVIEW_SAMPLE_SIZE:
                    # 节假日、开盘前今天还没有行情：用最近一个有行情的交易日（指数接口此时返回的也是它的收盘）
                    latest = self._latest_overview_date(session, today)
                    if latest:
                        stat_date = latest
                        daily_all = [r for r in session.query(SD).filter(SD.trade_date == latest).all()
                                     if self._extract_bare_equity_code(r.code)]
                if daily_all and len(daily_all) > MIN_MARKET_OVERVIEW_SAMPLE_SIZE:
                    total_amount = sum((r.amount or 0) for r in daily_all)
                    overview["total_amount_yi"] = round(total_amount / 1e8, 0)

                    changes = [r.change_pct for r in daily_all if r.change_pct is not None]
                    if changes:
                        overview["up_count"] = sum(1 for c in changes if c > 0)
                        overview["down_count"] = sum(1 for c in changes if c < 0)
                        overview["flat_count"] = sum(1 for c in changes if c == 0)
                        overview["avg_change_pct"] = round(sum(changes) / len(changes), 2)
                        overview["limit_up_count"] = sum(1 for c in changes if c >= LIMIT_UP_CHANGE_THRESHOLD)
                        overview["limit_down_count"] = sum(1 for c in changes if c <= LIMIT_DOWN_CHANGE_THRESHOLD)

                    logger.info(
                        f"市场概况(DB): 成交额={overview['total_amount_yi']:.0f}亿 "
                        f"涨/跌/平={overview['up_count']}/{overview['down_count']}/{overview['flat_count']} "
                        f"涨停/跌停={overview['limit_up_count']}/{overview['limit_down_count']}"
                    )
        except Exception as e:
            logger.warning(f"DB行情统计失败: {e}")

        # 如果 DB 数据不足，等待下次行情采集（不在此处重复调用 AKShare 避免并发冲突）
        if overview["total_amount_yi"] == 0:
            logger.debug("市场概况: DB数据不足，等待行情采集后再更新")

        # ======== 2. 主要指数 + 板块（延迟独立采集，不与行情采集并发） ========
        self._collect_index_and_sectors(overview)

        # ======== 4. 北向资金（DB兜底，direct在上面已采集） ========
        if overview["northbound_net_yi"] == 0.0:
            try:
                from src.database.db import get_db_session
                from src.database.models import NorthboundFlow
                with get_db_session(db_path) as session:
                    nb = session.query(NorthboundFlow).filter(
                        NorthboundFlow.trade_date == stat_date
                    ).first()
                    if nb and nb.total_net_inflow is not None:
                        overview["northbound_net_yi"] = round(nb.total_net_inflow / 1e4, 2)
            except Exception:
                pass

        # ======== 5. 市场情绪判定 ========
        up = overview["up_count"]
        down = overview["down_count"]
        lu = overview["limit_up_count"]
        ld = overview["limit_down_count"]
        amount = overview["total_amount_yi"]

        if amount > 0 and amount < EXTREME_LOW_LIQUIDITY_AMOUNT_YI:
            emotion = "极度缩量"
        elif 0 < amount < LOW_LIQUIDITY_AMOUNT_YI:
            emotion = "缩量谨慎"
        elif up > 0 and up > down * EMOTION_MULTIPLIER_THRESHOLD and lu > BULL_MARKET_LIMIT_UP_MIN and ld < BULL_MARKET_LIMIT_DOWN_MAX:
            emotion = "亢奋"
        elif down > 0 and down > up * EMOTION_MULTIPLIER_THRESHOLD or ld >= PANIC_LIMIT_DOWN_MIN and ld > lu:
            emotion = "恐慌"
        elif up > 0 and up > down * BIAS_MULTIPLIER_THRESHOLD:
            emotion = "偏多"
        elif down > 0 and down > up * BIAS_MULTIPLIER_THRESHOLD:
            emotion = "偏空"
        elif up > 0 or down > 0:
            emotion = "震荡分化"
        else:
            emotion = "待开盘"
        overview["market_emotion"] = emotion
        overview["trade_date"] = stat_date  # 涨跌家数、成交额等统计所属的交易日
        overview["update_time"] = datetime.now().strftime("%H:%M:%S")

        StockDataCollector._market_overview_cache = overview
        return overview

    @staticmethod
    def _latest_overview_date(session, before: str) -> str | None:
        """before 之前最近一个行情样本足够的交易日（跳过旧版本在节假日写入的重复数据）"""
        from sqlalchemy import func

        from src import trading_calendar
        from src.database.models import StockDaily as SD

        dates = (session.query(SD.trade_date, func.count(SD.code)).filter(SD.trade_date < before)
                 .group_by(SD.trade_date).order_by(SD.trade_date.desc()).limit(10).all())
        for trade_date, count in dates:
            if count > MIN_MARKET_OVERVIEW_SAMPLE_SIZE and trading_calendar.is_trade_day(trade_date):
                return trade_date
        return None

    def _collect_index_and_sectors(self, overview: dict):
        """
        采集指数和板块数据。
        多源策略：东方财富直接API > 新浪 > AKShare（兜底）
        """
        import requests as _req

        # ======== 主要指数 ========
        idx_ok = False

        # 源1: 东方财富 ulist 直接请求（最稳定）
        # 同时获取 f6(成交额) 用于实时计算两市总成交额
        # 额外请求 0.399106(深证综指，覆盖全部深市股票) 以获取深市总成交额
        try:
            url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
            params = {
                "fltt": 2,
                "fields": "f2,f3,f4,f6,f12,f14",
                "secids": "1.000001,0.399001,0.399006,0.399106",
            }
            data = get_em_client().request_json(
                url,
                params=params,
                timeout=8000,
                referer="https://quote.eastmoney.com/",
            ) or {}
            diff = data.get("data", {}).get("diff", [])
            code_map = {
                "000001": ("sh_index", "sh_change_pct"),
                "399001": ("sz_index", "sz_change_pct"),
                "399006": ("cy_index", "cy_change_pct"),
            }
            sh_turnover = 0.0  # 上证指数成交额（=沪市总成交额）
            sz_turnover = 0.0  # 深证综指成交额（=深市总成交额）
            for item in diff:
                code = str(item.get("f12", ""))
                if code in code_map:
                    k_val, k_pct = code_map[code]
                    overview[k_val] = str(item.get("f2", ""))
                    overview[k_pct] = round(float(item.get("f3", 0)), 2)
                # 提取成交额用于计算两市总成交额
                f6 = float(item.get("f6", 0) or 0)
                if code == "000001":
                    sh_turnover = f6
                elif code == "399106":
                    sz_turnover = f6

            # 实时两市总成交额（优先使用，比DB汇总更及时准确）
            if sh_turnover > 0 or sz_turnover > 0:
                total_realtime = sh_turnover + sz_turnover
                overview["total_amount_yi"] = round(total_realtime / 1e8, 0)
                logger.info(
                    f"两市成交额(实时): 沪={sh_turnover/1e8:,.0f}亿 "
                    f"深={sz_turnover/1e8:,.0f}亿 "
                    f"合计={overview['total_amount_yi']:,.0f}亿"
                )

            if overview.get("sh_index"):
                idx_ok = True
                logger.info(
                    f"指数采集(eastmoney): 上证={overview['sh_index']}({overview['sh_change_pct']:+.2f}%) "
                    f"深证={overview['sz_index']}({overview['sz_change_pct']:+.2f}%) "
                    f"创业={overview['cy_index']}({overview['cy_change_pct']:+.2f}%)"
                )
            source_health.record("指数行情", "东方财富", idx_ok)
        except Exception as e:
            source_health.record("指数行情", "东方财富", False, str(e))
            logger.debug(f"指数采集(eastmoney)失败: {e}")

        # 源2: 新浪 hq.sinajs.cn
        if not idx_ok:
            try:
                url = "https://hq.sinajs.cn/list=s_sh000001,s_sz399001,s_sz399006"
                r = _req.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=8)
                r.encoding = "gbk"
                lines = r.text.strip().split("\n")
                sina_map = [
                    ("sh_index", "sh_change_pct"),
                    ("sz_index", "sz_change_pct"),
                    ("cy_index", "cy_change_pct"),
                ]
                for i, line in enumerate(lines):
                    if i >= SINA_INDEX_MAX_LINES:
                        break
                    parts = line.split('"')[1].split(",") if '"' in line else []
                    if len(parts) >= SINA_MIN_PARTS:
                        k_val, k_pct = sina_map[i]
                        overview[k_val] = parts[1]
                        overview[k_pct] = round(float(parts[3]), 2)
                if overview.get("sh_index"):
                    idx_ok = True
                    logger.info(f"指数采集(sina): 上证={overview['sh_index']}")
                source_health.record("指数行情", "新浪", idx_ok)
            except Exception as e:
                source_health.record("指数行情", "新浪", False, str(e))
                logger.debug(f"指数采集(sina)失败: {e}")

        # 源3: Playwright 东方财富兜底
        if not idx_ok:
            try:
                df_idx = get_em_client().stock_zh_index_spot_em()
                if df_idx is not None and not df_idx.empty:
                    ak_map = {
                        "上证指数": ("sh_index", "sh_change_pct"),
                        "深证成指": ("sz_index", "sz_change_pct"),
                        "创业板指": ("cy_index", "cy_change_pct"),
                    }
                    # 同时提取成交额用于兜底计算两市总成交额
                    ak_sh_amount = 0.0
                    ak_sz_amount = 0.0
                    for _, row in df_idx.iterrows():
                        name = str(row.get("名称", ""))
                        if name in ak_map:
                            k_val, k_pct = ak_map[name]
                            overview[k_val] = str(row.get("最新价", ""))
                            pct = _safe_float(row.get("涨跌幅"))
                            overview[k_pct] = round(pct, 2) if pct else 0.0
                        # 提取成交额（兜底两市总成交额）
                        if name == "上证指数":
                            ak_sh_amount = _safe_float(row.get("成交额")) or 0.0
                        elif name in ("深证综指", "深证成指"):
                            val = _safe_float(row.get("成交额")) or 0.0
                            ak_sz_amount = max(ak_sz_amount, val)
                    # 如果东方财富源1未获取到成交额，用AKShare兜底
                    if overview.get("total_amount_yi", 0) == 0 and (ak_sh_amount > 0 or ak_sz_amount > 0):
                        total_ak = ak_sh_amount + ak_sz_amount
                        overview["total_amount_yi"] = round(total_ak / 1e8, 0)
                        logger.info(f"两市成交额(akshare兜底): {overview['total_amount_yi']:,.0f}亿")
                    logger.info(f"指数采集(akshare): 上证={overview.get('sh_index','')}")
                source_health.record("指数行情", "东方财富(Playwright)", bool(overview.get("sh_index")))
            except Exception as e:
                source_health.record("指数行情", "东方财富(Playwright)", False, str(e))
                logger.debug(f"指数采集(akshare)失败: {e}")

        # ======== 板块涨幅排行 ========
        sec_ok = False

        # 源1: 东方财富板块直接请求
        try:
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            params = {
                "pn": 1, "pz": 200, "po": 1, "np": 1,
                "fltt": 2, "invt": 2, "fid": "f3",
                "fs": "m:90+t:2",
                "fields": "f3,f14",
            }
            data = get_em_client().request_json(
                url,
                params=params,
                timeout=8000,
                referer="https://quote.eastmoney.com/",
            ) or {}
            diff = data.get("data", {}).get("diff", [])
            if diff:
                # diff 已按 f3(涨跌幅) 降序排列
                overview["top_sectors"] = [
                    {"name": str(d.get("f14", "")), "pct": round(float(d.get("f3", 0)), 2)}
                    for d in diff[:5]
                ]
                overview["bottom_sectors"] = [
                    {"name": str(d.get("f14", "")), "pct": round(float(d.get("f3", 0)), 2)}
                    for d in diff[-5:]
                ]
                sec_ok = True
                logger.info(f"板块采集(eastmoney): 领涨={[s['name'] for s in overview['top_sectors'][:3]]}")
            source_health.record("板块排行", "东方财富", sec_ok)
        except Exception as e:
            source_health.record("板块排行", "东方财富", False, str(e))
            logger.debug(f"板块采集(eastmoney)失败: {e}")

        # 源2: 从DB涨停股的板块统计
        if not sec_ok:
            try:
                from src.database.db import get_db_session
                db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
                today = date.today().strftime("%Y-%m-%d")
                with get_db_session(db_path) as session:
                    from src.database.models import LimitUpStock as LU
                    lu_all = session.query(LU).filter(LU.trade_date == today).all()
                    sector_count: dict[str, int] = {}
                    for lu in lu_all:
                        s = lu.sector or "未知"
                        sector_count[s] = sector_count.get(s, 0) + 1
                    if sector_count:
                        sorted_secs = sorted(sector_count.items(), key=lambda x: x[1], reverse=True)
                        overview["top_sectors"] = [
                            {"name": name, "pct": count}
                            for name, count in sorted_secs[:5]
                        ]
                        sec_ok = True
                        logger.info(f"板块采集(DB涨停统计): {sorted_secs[:5]}")
            except Exception as e:
                logger.debug(f"板块采集(DB)失败: {e}")

        # ======== 北向资金(直接请求) ========
        try:
            url = "https://push2his.eastmoney.com/api/qt/kamt.kline/get"
            params = {"fields1": "f1,f3,f5", "fields2": "f51,f52", "klt": 101, "lmt": 1}
            data = (get_em_client().request_json(
                url,
                params=params,
                timeout=8000,
                referer="https://quote.eastmoney.com/",
            ) or {}).get("data", {})
            # hk2sh + hk2sz = 北向净流入
            nb_total = 0.0
            for key in ["hk2sh", "hk2sz"]:
                vals = data.get(key, [])
                if vals:
                    parts = vals[-1].split(",")
                    if len(parts) >= NORTHBOUND_PARTS_MIN_LENGTH:
                        nb_total += float(parts[1])
            overview["northbound_net_yi"] = round(nb_total, 2)
            logger.info(f"北向资金(direct): {overview['northbound_net_yi']}亿")
            source_health.record("北向资金", "东方财富", bool(data))
        except Exception as e:
            source_health.record("北向资金", "东方财富", False, str(e))
            logger.debug(f"北向资金(direct)失败: {e}")

    def _get_all_stock_codes(self) -> list[str]:
        """获取全部A股代码列表，用于腾讯财经批量查询。
        
        先从数据库获取已有代码，若无数据则尝试从公开接口获取。
        返回格式: ['sz002131', 'sh600000', ...]
        """
        # 优先从数据库取（最快）
        try:
            db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
            with get_db_session(db_path) as session:
                from sqlalchemy import func
                latest_td = session.query(func.max(StockDaily.trade_date)).scalar()
                if latest_td:
                    raw_codes = [
                        (r[0] or "")
                        for r in session.query(StockDaily.code)
                        .filter(StockDaily.trade_date == latest_td)
                        .all()
                    ]
                    codes = self._dedupe_keep_order(
                        [self._to_tencent_code(c) for c in raw_codes]
                    )
                    if len(codes) >= MIN_DB_CODE_COUNT:
                        logger.info(f"从DB获取 {len(codes)} 个股票代码")
                        return codes
        except Exception as e:
            logger.debug(f"从DB获取代码列表失败: {e}")

        # 备用1: AKShare 基础代码表（稳定，不依赖实时行情接口）
        try:
            df_codes = ak.stock_info_a_code_name()
            if df_codes is not None and not df_codes.empty and "code" in df_codes.columns:
                codes = self._dedupe_keep_order(
                    [self._to_tencent_code(str(c)) for c in df_codes["code"].tolist()]
                )
                if len(codes) >= MIN_DB_CODE_COUNT:
                    logger.info(f"从AKShare代码表获取 {len(codes)} 个股票代码")
                    return codes
        except Exception as e:
            logger.debug(f"AKShare代码表获取失败: {e}")

        # 备用2: 东方财富 datacenter（历史上不稳定，增加健壮性校验）
        codes = []
        try:
            data = get_em_client().request_json(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                params={
                    "reportName": "RPT_LICO_FN_CPD",
                    "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_MARKET_CODE",
                    "pageSize": "10000",
                    "pageNumber": "1",
                    "source": "WEB",
                    "client": "WEB",
                },
                timeout=15000,
                referer="https://data.eastmoney.com/",
            )
            if data:
                for item in (data.get("result") or {}).get("data") or []:
                    sc = item.get("SECURITY_CODE", "")
                    mkt = str(item.get("TRADE_MARKET_CODE", ""))
                    if not sc:
                        continue
                    if mkt.startswith("069"):
                        codes.append(f"sh{sc}")
                    else:
                        codes.append(f"sz{sc}")
        except Exception as e:
            logger.debug(f"东方财富代码列表获取失败: {e}")

        codes = self._dedupe_keep_order([self._to_tencent_code(c) for c in codes])
        # 健壮性: 防止出现“500个重复 sh000001”这类脏结果
        if len(codes) >= MIN_EM_CODE_COUNT:
            logger.info(f"从东方财富datacenter获取 {len(codes)} 个股票代码")
            return codes
        if codes:
            logger.warning(f"datacenter 代码数量异常({len(codes)}), 忽略该结果")

        # 最后兜底: 生成主板/创业板/科创板/北交所常见代码段
        for prefix, start, end in [
            ("sh", 600000, 605000), ("sh", 688000, 689100),
            ("sz", 0, 4500), ("sz", 300000, 302000),
            ("bj", 430000, 431000), ("bj", 830000, 840000),
        ]:
            for n in range(start, end):
                codes.append(f"{prefix}{n:06d}")
        codes = self._dedupe_keep_order(codes)
        logger.warning(f"使用穷举代码列表: {len(codes)} 个")
        return codes

    def _collect_realtime_quotes(self, trade_date: str, db_path: str):
        """
        采集 A 股实时行情 —— 多源兜底策略。
        默认顺序：腾讯财经(HTTP) -> 东方财富 -> 新浪 -> efinance -> 通达信，由 data_sources.realtime 调整
        任一源成功即返回，全部失败才报错。
        """

        # ——— 数据源定义 ———
        def _try_tencent():
            """腾讯财经API（qt.gtimg.cn），最稳定的实时行情数据源。"""
            from concurrent.futures import ThreadPoolExecutor, as_completed

            import httpx

            # 先获取全部A股代码列表
            all_codes = self._get_all_stock_codes()
            if not all_codes:
                raise ConnectionError("无法获取股票代码列表")

            batch_size = 80  # 腾讯每次可查约80只

            # 构建所有批次查询串
            batches = [
                ",".join(all_codes[i:i + batch_size])
                for i in range(0, len(all_codes), batch_size)
            ]

            def _fetch_batch(query: str) -> list[dict]:
                """获取一个批次的行情数据。"""
                batch_records = []
                try:
                    resp = httpx.get(
                        f"https://qt.gtimg.cn/q={query}",
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                            "Referer": "https://gu.qq.com/",
                        },
                        timeout=15,
                    )
                    if resp.status_code != HTTP_OK_STATUS:
                        return batch_records
                    # 解析腾讯行情格式: v_sz002131="51~利欧股份~002131~9.08~...~10.06~..."
                    # 字段索引: 1=名称, 3=最新价, 5=今开,
                    #   32=涨跌幅, 33=最高, 34=最低, 36=成交量(手),
                    #   37=成交额(万), 38=换手率, 39=市盈率(动态), 44=流通市值(亿), 45=总市值(亿), 46=市净率
                    for line in resp.text.strip().split(";"):
                        line = line.strip()
                        if "~" not in line:
                            continue
                        parts = line.split("~")
                        if len(parts) < TENCENT_MIN_FIELDS:
                            continue
                        eq_idx = line.find('="')
                        if eq_idx < 0:
                            continue
                        var_name = line[:eq_idx].strip()
                        code_full = var_name.replace("v_", "").lower()
                        bare_code = self._extract_bare_equity_code(code_full)
                        if not bare_code:
                            continue
                        name = normalize_name(parts[1])
                        price = _safe_float(parts[3])
                        if not name or price is None or price <= 0:
                            continue
                        # 单位转换: 腾讯成交量=手→股(*100)（科创板已经是股，不再乘）, 成交额=万→元(*10000), 市值=亿→元(*1e8)
                        vol_raw = _safe_float(parts[36])
                        amt_raw = _safe_float(parts[37])
                        tmv_raw = _safe_float(parts[TENCENT_TOTAL_MV_INDEX]) if len(parts) > TENCENT_TOTAL_MV_INDEX else None
                        cmv_raw = _safe_float(parts[TENCENT_CIRC_MV_INDEX]) if len(parts) > TENCENT_CIRC_MV_INDEX else None
                        batch_records.append({
                            "代码": bare_code,
                            "名称": name,
                            "最新价": price,
                            "涨跌幅": _safe_float(parts[32]),
                            "最高": _safe_float(parts[33]),
                            "最低": _safe_float(parts[34]),
                            "今开": _safe_float(parts[5]),
                            "成交量": (vol_raw if board_of(bare_code) == "科创板" else vol_raw * 100) if vol_raw else None,
                            "成交额": amt_raw * 10000 if amt_raw else None,
                            "换手率": _safe_float(parts[38]),
                            "总市值": tmv_raw * 1e8 if tmv_raw else None,
                            "流通市值": cmv_raw * 1e8 if cmv_raw else None,
                            "市盈率": _safe_float(parts[TENCENT_PE_INDEX]) if len(parts) > TENCENT_PE_INDEX else None,
                            "市净率": _safe_float(parts[TENCENT_PB_INDEX]) if len(parts) > TENCENT_PB_INDEX else None,
                        })
                except Exception as e:
                    logger.debug(f"腾讯行情批次异常: {e}")
                return batch_records

            # 并发拉取（8线程，平衡速度和服务端友好度）
            all_records = []
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(_fetch_batch, q): q for q in batches}
                for future in as_completed(futures):
                    with suppress(Exception):
                        all_records.extend(future.result())

            if not all_records:
                raise ConnectionError("腾讯财经API无数据返回")
            logger.info(f"腾讯财经API获取 {len(all_records)} 条行情")
            return pd.DataFrame(all_records)

        def _try_em():
            return get_em_client().stock_zh_a_spot_em()

        def _try_sina():
            df = ak.stock_zh_a_spot()
            col_map = {
                "代码": "代码", "名称": "名称",
                "最高": "最高", "最低": "最低",
                "今开": "今开", "最新价": "最新价",
                "成交量": "成交量", "成交额": "成交额",
                "涨跌幅": "涨跌幅", "换手率": "换手率",
                "总市值": "总市值", "流通市值": "流通市值",
            }
            rename = {}
            for target, src_name in col_map.items():
                if src_name in df.columns:
                    rename[src_name] = target
                elif target != src_name and target in df.columns:
                    pass
            if rename:
                df = df.rename(columns=rename)
            return df

        def _try_efinance():
            from src.collectors.extra_sources import fetch_spot_efinance

            return fetch_spot_efinance()

        def _try_pytdx():
            from src.collectors.extra_sources import fetch_spot_pytdx

            with get_db_session(db_path) as session:
                names = {code: name for code, name in session.query(StockInfo.code, StockInfo.name).all()}
            codes = [self._extract_bare_equity_code(c) for c in self._get_all_stock_codes()]
            return fetch_spot_pytdx([c for c in codes if c], names)

        available = {
            "tencent": ("腾讯财经(HTTP)", _try_tencent),
            "eastmoney": ("东方财富(Playwright)", _try_em),
            "sina": ("新浪(AKShare)", _try_sina),
            "efinance": ("efinance", _try_efinance),
            "pytdx": ("通达信(pytdx)", _try_pytdx),
        }
        order = (self.config.get("data_sources") or {}).get("realtime") or list(REALTIME_SOURCES)
        sources = [available[name] for name in order if name in available] or [available["tencent"]]

        # 行情按交易日写库，不能用之前缓存的数据冒充当前行情，所以不允许 stale
        fetched = fetch_with_fallback("实时行情", sources, attempts=2, retry_wait=1.5)
        if not fetched.ok:
            return
        df = fetched.data
        logger.info(f"实时行情数据源 [{fetched.source}] 成功, {len(df)} 条")

        try:
            # 使用列名→列索引映射 + itertuples（比 iterrows 快 5~10 倍）
            col_map = {c: i for i, c in enumerate(df.columns)}
            records = []
            for tup in df.itertuples(index=False):
                raw_code = str(tup[col_map["代码"]]) if "代码" in col_map else ""
                code = self._extract_bare_equity_code(raw_code)
                if not code:
                    continue
                records.append(StockDaily(
                    code=code,
                    name=normalize_name(tup[col_map["名称"]]) if "名称" in col_map else "",
                    trade_date=trade_date,
                    open=_safe_float(tup[col_map["今开"]] if "今开" in col_map else None),
                    close=_safe_float(tup[col_map["最新价"]] if "最新价" in col_map else None),
                    high=_safe_float(tup[col_map["最高"]] if "最高" in col_map else None),
                    low=_safe_float(tup[col_map["最低"]] if "最低" in col_map else None),
                    volume=_safe_float(tup[col_map["成交量"]] if "成交量" in col_map else None),
                    amount=_safe_float(tup[col_map["成交额"]] if "成交额" in col_map else None),
                    change_pct=_safe_float(tup[col_map["涨跌幅"]] if "涨跌幅" in col_map else None),
                    turnover=_safe_float(tup[col_map["换手率"]] if "换手率" in col_map else None),
                    total_mv=_safe_float(tup[col_map["总市值"]] if "总市值" in col_map else None),
                    circ_mv=_safe_float(tup[col_map["流通市值"]] if "流通市值" in col_map else None),
                    pe=_valuation(tup[col_map["市盈率"]] if "市盈率" in col_map else None),
                    pb=_valuation(tup[col_map["市净率"]] if "市净率" in col_map else None),
                ))
            # 同一批次可能出现重复 code，先去重，避免唯一键冲突
            dedup_map = {}
            for rec in records:
                dedup_map[rec.code] = rec
            records = list(dedup_map.values())

            with get_db_session(db_path) as session:
                # 批量预取已有记录，避免逐条 query
                existing_map = {}
                existing_rows = (
                    session.query(StockDaily)
                    .filter(StockDaily.trade_date == trade_date)
                    .all()
                )
                legacy_deleted = 0
                for er in existing_rows:
                    code = (er.code or "").strip()
                    # 清理历史脏数据（如 sh000001 指数行），避免干扰A股统计
                    if not (len(code) == STOCK_CODE_LENGTH and code.isdigit()):
                        session.delete(er)
                        legacy_deleted += 1
                        continue
                    existing_map[code] = er

                new_count = 0
                upd_count = 0
                from datetime import datetime as _dt
                for rec in records:
                    existing = existing_map.get(rec.code)
                    if existing:
                        for col in ["open", "close", "high", "low", "volume",
                                    "amount", "change_pct", "turnover", "total_mv", "circ_mv", "pe", "pb"]:
                            setattr(existing, col, getattr(rec, col))
                        existing.updated_at = _dt.now()
                        upd_count += 1
                    else:
                        session.add(rec)
                        existing_map[rec.code] = rec
                        new_count += 1

            if legacy_deleted:
                logger.info(f"实时行情清理历史脏数据: {legacy_deleted} 条")
            logger.info(f"实时行情采集完成: {len(records)} 只(新增{new_count}, 更新{upd_count})")

        except Exception as e:
            logger.error(f"实时行情入库失败: {e}")

    @staticmethod
    def _limit_up_rows_only(df: pd.DataFrame | None) -> pd.DataFrame | None:
        """强势股池包含未涨停的强势股，作为涨停池备用源时只保留涨幅达到涨停幅度的行。"""
        if df is None or df.empty or not {"代码", "涨跌幅"} <= set(df.columns):
            return df
        names = df["名称"] if "名称" in df.columns else [""] * len(df)
        keep = [
            _safe_float(chg) is not None and _safe_float(chg) >= daily_limit_pct(str(code), str(name)) * 100 - LIMIT_UP_PCT_TOLERANCE
            for code, name, chg in zip(df["代码"], names, df["涨跌幅"])
        ]
        return df[keep]

    def _collect_limit_up_pool(self, trade_date: str, db_path: str):
        """采集涨停池数据（含涨停原因） —— 多源兜底"""
        day = trade_date.replace("-", "")
        fetched = fetch_with_fallback(
            "涨停池",
            [
                ("东方财富涨停池", lambda: get_em_client().stock_zt_pool_em(date=day)),
                ("东方财富强势股池(仅涨停)", lambda: self._limit_up_rows_only(get_em_client().stock_zt_pool_strong_em(date=day))),
            ],
            attempts=2,
            retry_wait=1,
        )
        df = fetched.data

        try:
            if df is None or df.empty:
                logger.info("今日暂无涨停池数据（所有源均无数据）")
                return

            # 获取强势股池中的「入选理由」作为涨停原因补充
            strong_reasons = {}
            try:
                df_strong = get_em_client().stock_zt_pool_strong_em(date=trade_date.replace("-", ""))
                if df_strong is not None and not df_strong.empty:
                    c_code = df_strong.columns.get_loc("代码") if "代码" in df_strong.columns else -1
                    c_reason = df_strong.columns.get_loc("入选理由") if "入选理由" in df_strong.columns else -1
                    if c_code >= 0 and c_reason >= 0:
                        for tup in df_strong.itertuples(index=False):
                            code = str(tup[c_code])
                            reason = str(tup[c_reason])
                            if code and reason:
                                strong_reasons[code] = reason
                    logger.info(f"强势股池获取 {len(strong_reasons)} 条入选理由")
            except Exception as e:
                logger.debug(f"强势股池采集失败(非关键): {e}")

            # 同花顺涨停原因（题材标签），用于识别跨行业的题材主线；失败不影响涨停池入库
            concepts_by_code = fetch_with_fallback(
                "涨停原因", [("同花顺", lambda: fetch_ths_limit_up_reasons(trade_date))], attempts=2, retry_wait=1,
            ).data or {}

            cm = {c: i for i, c in enumerate(df.columns)}
            records = []
            for tup in df.itertuples(index=False):
                code = str(tup[cm["代码"]]) if "代码" in cm else ""
                sector = str(tup[cm["所属行业"]]) if "所属行业" in cm else ""
                continuous_days = _safe_int(tup[cm["连板数"]] if "连板数" in cm else 1) or 1
                concepts = concepts_by_code.get(code, "")

                # 组合涨停原因：同花顺题材 > 强势股理由 > 连板描述 > 行业
                reason = concepts or strong_reasons.get(code, "")
                if not reason:
                    reason = f"{continuous_days}连板 | {sector}" if continuous_days > 1 else sector

                records.append(LimitUpStock(
                    code=code,
                    name=normalize_name(tup[cm["名称"]]) if "名称" in cm else "",
                    trade_date=trade_date,
                    close=_safe_float(tup[cm["最新价"]] if "最新价" in cm else None),
                    change_pct=_safe_float(tup[cm["涨跌幅"]] if "涨跌幅" in cm else None),
                    continuous_days=continuous_days,
                    seal_amount=_safe_float(tup[cm["封板资金"]] if "封板资金" in cm else None),
                    first_limit_time=str(tup[cm["首次封板时间"]] if "首次封板时间" in cm else ""),
                    last_limit_time=str(tup[cm["最后封板时间"]] if "最后封板时间" in cm else ""),
                    open_count=_safe_int(tup[cm["炸板次数"]] if "炸板次数" in cm else 0),
                    sector=sector,
                    reason=reason,
                    concepts=concepts,
                    circ_mv=_safe_float(tup[cm["流通市值"]] if "流通市值" in cm else None),
                ))

            with get_db_session(db_path) as session:
                # 先清除今日旧数据（防止开盘前采集到的昨日残留数据）
                old_count = session.query(LimitUpStock).filter(
                    LimitUpStock.trade_date == trade_date
                ).delete()
                if old_count:
                    logger.info(f"涨停池: 清除今日旧数据 {old_count} 条，准备写入最新 {len(records)} 条")
                # 全量写入最新数据
                session.add_all(records)

            logger.info(f"涨停池采集完成: {len(records)} 只涨停股")

        except Exception as e:
            logger.error(f"涨停池采集失败: {e}")

    def _collect_dragon_tiger(self, trade_date: str, db_path: str):
        """采集龙虎榜数据"""
        try:
            df = get_em_client().stock_lhb_detail_em(
                start_date=trade_date.replace("-", ""),
                end_date=trade_date.replace("-", ""),
            )
            if df is None or df.empty:
                logger.info("今日暂无龙虎榜数据")
                return

            cm = {c: i for i, c in enumerate(df.columns)}
            records = [
                DragonTigerBoard(
                    code=str(tup[cm["代码"]]) if "代码" in cm else "",
                    name=normalize_name(tup[cm["名称"]]) if "名称" in cm else "",
                    trade_date=trade_date,
                    reason=(
                        str(tup[cm.get("解读", cm.get("上榜原因", 0))])
                        if ("解读" in cm or "上榜原因" in cm)
                        else ""
                    ),
                    buy_total=_safe_float(tup[cm["买入额"]] if "买入额" in cm else None),
                    sell_total=_safe_float(tup[cm["卖出额"]] if "卖出额" in cm else None),
                    net_amount=_safe_float(tup[cm["净额"]] if "净额" in cm else None),
                )
                for tup in df.itertuples(index=False)
            ]

            with get_db_session(db_path) as session:
                session.add_all(records)

            logger.info(f"龙虎榜采集完成: {len(records)} 条记录")

        except Exception as e:
            logger.error(f"龙虎榜采集失败: {e}")

    def _collect_northbound_flow(self, trade_date: str, db_path: str):
        """采集北向资金数据（沪股通+深股通） —— 多源兜底"""
        total_net: float | None = None

        # 源1：资金流向汇总
        try:
            df = get_em_client().stock_hsgt_fund_flow_summary_em()
            if df is not None and not df.empty:
                today_north = df[
                    (df["交易日"].astype(str) == trade_date) &
                    (df["资金方向"] == "北向")
                ]
                if not today_north.empty:
                    total_net = 0
                    for _, row in today_north.iterrows():
                        val = _safe_float(row.get("成交净买额"))
                        if val is not None:
                            total_net += val
                    if abs(total_net) > NORTHBOUND_UNIT_SPLIT_THRESHOLD:
                        total_net = total_net / 1e8
                    logger.info(f"北向资金源1(summary)成功: {total_net:.2f} 亿")
        except Exception as e:
            logger.warning(f"北向资金源1失败: {e}")

        # 源2：沪深港通日频数据
        if total_net is None:
            try:
                df2 = get_em_client().stock_hsgt_hist_em(symbol="北向资金")
                if df2 is not None and not df2.empty:
                    today_row = df2[df2["日期"].astype(str) == trade_date]
                    if not today_row.empty:
                        val = _safe_float(today_row.iloc[0].get("当日净流入", today_row.iloc[0].get("净流入")))
                        if val is not None:
                            total_net = val if abs(val) < NORTHBOUND_UNIT_SPLIT_THRESHOLD else val / 1e8
                            logger.info(f"北向资金源2(hist)成功: {total_net:.2f} 亿")
            except Exception as e2:
                logger.warning(f"北向资金源2失败: {e2}")

        # 源3：沪股通+深股通分别取
        if total_net is None:
            try:
                net = 0
                for channel in ["沪股通", "深股通"]:
                    try:
                        df3 = get_em_client().stock_hsgt_hist_em(symbol=channel)
                        if df3 is not None and not df3.empty:
                            today_row = df3[df3["日期"].astype(str) == trade_date]
                            if not today_row.empty:
                                val = _safe_float(today_row.iloc[0].get("当日净流入", today_row.iloc[0].get("净流入")))
                                if val is not None:
                                    net += val if abs(val) < NORTHBOUND_UNIT_SPLIT_THRESHOLD else val / 1e8
                    except Exception:
                        pass
                if net != 0:
                    total_net = net
                    logger.info(f"北向资金源3(分通道)成功: {total_net:.2f} 亿")
            except Exception as e3:
                logger.warning(f"北向资金源3失败: {e3}")

        if total_net is None:
            logger.warning(f"北向资金：所有源均无 {trade_date} 的数据")
            return

        try:
            record = NorthboundFlow(
                trade_date=trade_date,
                total_net_inflow=round(total_net, 2),
            )
            with get_db_session(db_path) as session:
                existing = session.query(NorthboundFlow).filter_by(
                    trade_date=trade_date
                ).first()
                if not existing:
                    session.add(record)
                else:
                    existing.total_net_inflow = record.total_net_inflow

            logger.info(f"北向资金采集完成: {trade_date}, 净流入 {total_net:.2f} 亿")
        except Exception as e:
            logger.error(f"北向资金入库失败: {e}")


def _safe_float(val) -> float | None:
    """安全转换为 float"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _valuation(val) -> float | None:
    """市盈率/市净率：接口用 0 或 "-" 表示没有数据。"""
    num = _safe_float(val)
    return num if num else None


def _safe_int(val) -> int | None:
    """安全转换为 int"""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
