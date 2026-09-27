"""
美股头部企业财报/研报数据采集器（增强版）
- 扩展跟踪范围：Mag7 + 半导体 + 中概股龙头
- 多源采集: AKShare → 东方财富直接API
- 新增: VIX恐慌指数、中概股指数、财报新闻抓取
"""

import re
from datetime import date, datetime, timedelta
from typing import Any

import akshare as ak
import pandas as pd
from loguru import logger

from src.collectors.base import BaseCollector
from src.collectors.em_client import get_em_client
from src.database.db import get_db_session
from src.database.models import GlobalNews, USMarketDaily, USStockEarnings

HTTP_OK_STATUS = 200
SINA_PARTS_MIN_LEN = 3
SINA_NAME_INDEX = 0
SINA_PRICE_INDEX = 1
SINA_CHANGE_INDEX = 2
SINA_QUOTE_TIME_INDEX = 3

HOT_CHANGE_ALERT_PCT = 2.0
HOT_IMPORTANCE_STRONG_PCT = 4


class USEarningsCollector(BaseCollector):
    """美股头部企业财报/研报采集器（增强版）"""

    SOURCE_NAME = "us_earnings"

    # 美股-A股产业链映射表（扩展）
    US_A_MAPPING = {
        # ---- Magnificent 7 ----
        "NVDA": {
            "name": "英伟达",
            "a_share_sectors": ["AI算力", "GPU", "光模块"],
            "a_share_stocks": ["中际旭创", "新易盛", "天孚通信", "工业富联"],
        },
        "AAPL": {
            "name": "苹果",
            "a_share_sectors": ["苹果产业链", "消费电子"],
            "a_share_stocks": ["立讯精密", "歌尔股份", "蓝思科技", "领益智造"],
        },
        "TSLA": {
            "name": "特斯拉",
            "a_share_sectors": ["新能源车产业链", "汽车零部件"],
            "a_share_stocks": ["宁德时代", "比亚迪", "拓普集团", "三花智控"],
        },
        "MSFT": {
            "name": "微软",
            "a_share_sectors": ["AI应用", "云计算", "办公软件"],
            "a_share_stocks": ["金山办公", "用友网络", "中科曙光"],
        },
        "GOOGL": {
            "name": "谷歌",
            "a_share_sectors": ["AI应用", "数据中心", "搜索广告"],
            "a_share_stocks": ["百度集团-SW"],
        },
        "AMZN": {
            "name": "亚马逊",
            "a_share_sectors": ["跨境电商", "云计算", "物流"],
            "a_share_stocks": ["华凯易佰", "跨境通"],
        },
        "META": {
            "name": "Meta",
            "a_share_sectors": ["VR/AR", "AI应用", "社交"],
            "a_share_stocks": ["歌尔股份"],
        },
        # ---- 半导体/AI 产业链 ----
        "AMD": {
            "name": "AMD",
            "a_share_sectors": ["AI算力", "芯片"],
            "a_share_stocks": ["寒武纪", "海光信息"],
        },
        "TSM": {
            "name": "台积电",
            "a_share_sectors": ["芯片代工", "半导体"],
            "a_share_stocks": ["中芯国际", "华虹公司"],
        },
        "AVGO": {
            "name": "博通",
            "a_share_sectors": ["芯片", "网络设备"],
            "a_share_stocks": ["中兴通讯", "紫光股份"],
        },
        "ASML": {
            "name": "ASML",
            "a_share_sectors": ["光刻机", "半导体设备"],
            "a_share_stocks": ["北方华创", "中微公司"],
        },
        "QCOM": {
            "name": "高通",
            "a_share_sectors": ["手机芯片", "5G"],
            "a_share_stocks": ["卓胜微", "韦尔股份"],
        },
        "MU": {
            "name": "美光科技",
            "a_share_sectors": ["存储芯片", "半导体"],
            "a_share_stocks": ["兆易创新", "北京君正"],
        },
        "ARM": {
            "name": "ARM",
            "a_share_sectors": ["芯片设计", "IP授权"],
            "a_share_stocks": ["寒武纪", "全志科技"],
        },
        # ---- 新能源 ----
        "LI": {
            "name": "理想汽车",
            "a_share_sectors": ["新能源车"],
            "a_share_stocks": ["拓普集团", "德赛西威"],
        },
        "NIO": {
            "name": "蔚来",
            "a_share_sectors": ["新能源车"],
            "a_share_stocks": ["宁德时代", "均胜电子"],
        },
        "XPEV": {
            "name": "小鹏汽车",
            "a_share_sectors": ["新能源车", "自动驾驶"],
            "a_share_stocks": ["德赛西威", "中科创达"],
        },
    }

    # 东方财富 secid 映射
    SECID_MAP = {
        "NVDA": "105.NVDA", "AAPL": "105.AAPL", "TSLA": "105.TSLA",
        "AMD": "105.AMD", "MSFT": "105.MSFT", "GOOGL": "105.GOOGL",
        "AMZN": "105.AMZN", "META": "105.META", "TSM": "105.TSM",
        "AVGO": "105.AVGO", "ASML": "105.ASML", "QCOM": "105.QCOM",
        "MU": "105.MU", "ARM": "105.ARM",
        "LI": "105.LI", "NIO": "105.NIO", "XPEV": "105.XPEV",
    }

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.last_run_metrics: dict[str, int] = {
            "us_market_daily": 0,
            "us_stock_earnings": 0,
            "us_hot_24h": 0,
            "us_estimate": 0,
            "us_earnings_news": 0,
        }

    def collect(self) -> list[dict[str, Any]]:
        """采集美股头部公司行情、三大指数、VIX、中概股指数、财报新闻"""
        db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        today = date.today().strftime("%Y-%m-%d")

        # 1. 美股三大指数 + VIX + 中概股指数
        self.last_run_metrics["us_market_daily"] = self._collect_us_market_overview(today, db_path)

        # 2. 重点跟踪的美股公司行情
        self.last_run_metrics["us_stock_earnings"] = self._collect_tracked_stocks(today, db_path)

        # 3. 24小时热点与估计（写入 GlobalNews）
        hot_count, estimate_count = self._collect_us_24h_hot_and_estimate(db_path)
        self.last_run_metrics["us_hot_24h"] = hot_count
        self.last_run_metrics["us_estimate"] = estimate_count

        # 4. 美股财报相关新闻（写入 GlobalNews 表）
        self.last_run_metrics["us_earnings_news"] = self._collect_earnings_news(db_path)

        return []

    # ============================================================
    # 美股三大指数 + VIX + 中概股
    # ============================================================

    @staticmethod
    def _parse_sina_quote_text(text: str) -> dict[str, dict[str, Any]]:
        """解析新浪行情文本，返回 symbol -> quote 信息。"""
        result: dict[str, dict[str, Any]] = {}
        for line in (text or "").splitlines():
            m = re.match(r'var hq_str_gb_([a-zA-Z0-9_]+)="(.*)";', line.strip())
            if not m:
                continue
            symbol = m.group(1).lower()
            payload = m.group(2).strip()
            if not payload:
                continue
            parts = payload.split(",")
            if len(parts) < SINA_PARTS_MIN_LEN:
                continue
            result[symbol] = {
                "name": parts[SINA_NAME_INDEX] if len(parts) > SINA_NAME_INDEX else "",
                "price": _safe_float(parts[SINA_PRICE_INDEX]) if len(parts) > SINA_PRICE_INDEX else None,
                "change_pct": _safe_float(parts[SINA_CHANGE_INDEX]) if len(parts) > SINA_CHANGE_INDEX else None,
                "quote_time": parts[SINA_QUOTE_TIME_INDEX] if len(parts) > SINA_QUOTE_TIME_INDEX else "",
            }
        return result

    def _fetch_sina_quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """通过新浪接口批量抓取美股/指数行情。"""
        symbols = [s.strip().lower() for s in symbols if s and s.strip()]
        if not symbols:
            return {}
        try:
            url = "https://hq.sinajs.cn/list=" + ",".join(f"gb_{s}" for s in symbols)
            resp = self.fetch_url(
                url,
                max_retries=2,
                headers={"Referer": "https://finance.sina.com.cn"},
            )
            if not resp:
                return {}
            return self._parse_sina_quote_text(resp.text)
        except Exception as e:
            logger.debug(f"新浪美股行情获取失败: {e}")
            return {}

    def _collect_us_market_overview(self, trade_date: str, db_path: str) -> int:
        """采集美股三大指数 + VIX + 中概股指数（多源）"""
        # 用 dict 暂存数据，避免 ORM 对象脱离 session 的问题
        data: dict = {}
        filled = False

        # 源1: AKShare 新浪美股指数
        try:
            df = ak.index_us_stock_sina()
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    name = str(row.get("名称", ""))
                    change_pct = _safe_float(row.get("涨跌幅"))
                    close_val = _safe_float(row.get("最新价"))
                    if "道琼斯" in name:
                        data["dow_jones_close"] = close_val
                        data["dow_jones_change_pct"] = change_pct
                        filled = True
                    elif "纳斯达克" in name:
                        data["nasdaq_close"] = close_val
                        data["nasdaq_change_pct"] = change_pct
                    elif "标普500" in name or "标普 500" in name:
                        data["sp500_close"] = close_val
                        data["sp500_change_pct"] = change_pct
        except Exception as e:
            logger.warning(f"美股指数(AKShare新浪)失败: {e}")

        # 源2: 东方财富直接API（含VIX和中概）
        try:
            url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
            secids = "100.DJIA,100.NDX,100.SPX,100.VIX"
            params = {
                "fltt": "2", "invt": "2",
                "fields": "f2,f3,f4,f12,f14",
                "secids": secids,
                "ut": "fa5fd1943c7b386f172d6893dbbd10d0",
            }
            api_data = get_em_client().request_json(
                url,
                params=params,
                timeout=10000,
                referer="https://quote.eastmoney.com/",
            ) or {}
            if api_data:
                for item in (api_data.get("data", {}) or {}).get("diff", []) or []:
                    code = item.get("f12", "")
                    close_val = _safe_float(item.get("f2"))
                    change_pct = _safe_float(item.get("f3"))

                    if "DJIA" in code and not filled:
                        data["dow_jones_close"] = close_val
                        data["dow_jones_change_pct"] = change_pct
                        filled = True
                    elif "NDX" in code and "nasdaq_close" not in data:
                        data["nasdaq_close"] = close_val
                        data["nasdaq_change_pct"] = change_pct
                    elif "SPX" in code and "sp500_close" not in data:
                        data["sp500_close"] = close_val
                        data["sp500_change_pct"] = change_pct
                    elif "VIX" in code:
                        data["vix"] = close_val

                if filled:
                    logger.debug("美股指数(东方财富API)补充成功")
        except Exception as e:
            logger.debug(f"美股指数(东方财富API)失败: {e}")

        # 中概股指数单独获取
        try:
            url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
            params = {
                "fltt": "2", "invt": "2",
                "fields": "f2,f3,f12,f14",
                "secids": "100.HXC",
                "ut": "fa5fd1943c7b386f172d6893dbbd10d0",
            }
            api_data = get_em_client().request_json(
                url,
                params=params,
                timeout=10000,
                referer="https://quote.eastmoney.com/",
            ) or {}
            if api_data:
                for item in (api_data.get("data", {}) or {}).get("diff", []) or []:
                    data["china_concept_index"] = _safe_float(item.get("f2"))
                    data["china_concept_change_pct"] = _safe_float(item.get("f3"))
        except Exception:
            pass

        # 原油和黄金价格
        try:
            url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
            params = {
                "fltt": "2", "invt": "2",
                "fields": "f2,f3,f12,f14",
                "secids": "113.CL00Y,113.GC00Y",
                "ut": "fa5fd1943c7b386f172d6893dbbd10d0",
            }
            api_data = get_em_client().request_json(
                url,
                params=params,
                timeout=10000,
                referer="https://quote.eastmoney.com/",
            ) or {}
            if api_data:
                for item in (api_data.get("data", {}) or {}).get("diff", []) or []:
                    code = item.get("f12", "")
                    if "CL" in code:
                        data["oil_price"] = _safe_float(item.get("f2"))
                    elif "GC" in code:
                        data["gold_price"] = _safe_float(item.get("f2"))
        except Exception:
            pass

        # 源3: 新浪直连接口（高可用兜底）
        try:
            sina = self._fetch_sina_quotes(["dji", "ixic", "inx", "hxc"])
            dji = sina.get("dji") or {}
            ixic = sina.get("ixic") or {}
            inx = sina.get("inx") or {}
            hxc = sina.get("hxc") or {}

            if dji.get("price") is not None:
                data["dow_jones_close"] = dji.get("price")
                data["dow_jones_change_pct"] = dji.get("change_pct")
                filled = True
            if ixic.get("price") is not None:
                data["nasdaq_close"] = ixic.get("price")
                data["nasdaq_change_pct"] = ixic.get("change_pct")
            if inx.get("price") is not None:
                data["sp500_close"] = inx.get("price")
                data["sp500_change_pct"] = inx.get("change_pct")
            if hxc.get("price") is not None:
                data["china_concept_index"] = hxc.get("price")
                data["china_concept_change_pct"] = hxc.get("change_pct")
        except Exception as e:
            logger.debug(f"美股指数(新浪直连)失败: {e}")

        if not filled:
            logger.warning("美股指数所有源均失败")
            return 0

        try:
            with get_db_session(db_path) as session:
                existing = session.query(USMarketDaily).filter_by(
                    trade_date=trade_date
                ).first()
                if existing:
                    for col, val in data.items():
                        if val is not None:
                            setattr(existing, col, val)
                else:
                    record = USMarketDaily(trade_date=trade_date, **{
                        k: v for k, v in data.items() if v is not None
                    })
                    session.add(record)
            logger.info(
                f"美股数据采集完成: 道指={data.get('dow_jones_change_pct', 'N/A')}% "
                f"纳指={data.get('nasdaq_change_pct', 'N/A')}% "
                f"VIX={data.get('vix', 'N/A')} "
                f"中概={data.get('china_concept_change_pct', 'N/A')}%"
            )
            return 1
        except Exception as e:
            logger.error(f"美股指数保存失败: {e}")
            return 0

    # ============================================================
    # 重点跟踪个股行情
    # ============================================================

    def _collect_tracked_stocks(self, trade_date: str, db_path: str) -> int:
        """采集重点跟踪的美股公司行情（多源）"""
        tracked_symbols = set(self.US_A_MAPPING)
        collected = set()

        # 源1: AKShare 东方财富美股
        try:
            df = ak.stock_us_spot_em()
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.get("代码", ""))
                    symbol = code.split(".")[-1] if "." in code else code
                    if symbol not in tracked_symbols:
                        continue
                    mapping = self.US_A_MAPPING.get(symbol, {})
                    change_pct = _safe_float(row.get("涨跌幅"))
                    close_val = _safe_float(row.get("最新价"))
                    self._save_us_stock(db_path, symbol, mapping, trade_date, change_pct, close_val)
                    collected.add(symbol)
        except Exception as e:
            logger.warning(f"美股行情(AKShare)失败: {e}")

        # 源2: 东方财富直接API（补齐）
        remaining = tracked_symbols - collected
        if remaining:
            try:
                secids = ",".join(
                    self.SECID_MAP.get(s, f"105.{s}") for s in remaining
                )
                url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
                params = {
                    "fltt": "2", "invt": "2",
                    "fields": "f2,f3,f12,f14",
                    "secids": secids,
                    "ut": "fa5fd1943c7b386f172d6893dbbd10d0",
                }
                data = get_em_client().request_json(
                    url,
                    params=params,
                    timeout=10000,
                    referer="https://quote.eastmoney.com/",
                ) or {}
                if data:
                    for item in (data.get("data", {}) or {}).get("diff", []) or []:
                        symbol = item.get("f12", "")
                        if symbol in remaining:
                            mapping = self.US_A_MAPPING.get(symbol, {})
                            change_pct = _safe_float(item.get("f3"))
                            close_val = _safe_float(item.get("f2"))
                            self._save_us_stock(db_path, symbol, mapping, trade_date, change_pct, close_val)
                            collected.add(symbol)
            except Exception as e:
                logger.warning(f"美股行情(东方财富API)失败: {e}")

        # 源3: 新浪直连兜底（逐只覆盖 remaining）
        remaining = tracked_symbols - collected
        if remaining:
            try:
                quotes = self._fetch_sina_quotes([s.lower() for s in remaining])
                for symbol in remaining:
                    quote = quotes.get(symbol.lower()) or {}
                    if quote.get("price") is None and quote.get("change_pct") is None:
                        continue
                    mapping = self.US_A_MAPPING.get(symbol, {})
                    self._save_us_stock(
                        db_path,
                        symbol,
                        mapping,
                        trade_date,
                        quote.get("change_pct"),
                        quote.get("price"),
                    )
                    collected.add(symbol)
            except Exception as e:
                logger.warning(f"美股行情(新浪直连)失败: {e}")

        logger.info(f"美股重点公司采集完成: {len(collected)}/{len(tracked_symbols)} 只")
        return len(collected)

    def _save_us_stock(self, db_path: str, symbol: str, mapping: dict,
                       trade_date: str, change_pct: float | None,
                       close_val: float | None = None):
        """保存单只美股数据"""
        import json
        try:
            with get_db_session(db_path) as session:
                payload = json.dumps({
                    "sectors": mapping.get("a_share_sectors", []),
                    "stocks": mapping.get("a_share_stocks", []),
                    "change_pct": change_pct,
                    "close": close_val,
                }, ensure_ascii=False)
                existing = session.query(USStockEarnings).filter_by(
                    symbol=symbol, report_date=trade_date
                ).first()
                if existing:
                    existing.company_name = mapping.get("name", symbol)
                    existing.after_hours_change_pct = change_pct
                    existing.a_share_impact = payload
                    existing.collected_at = datetime.now()
                else:
                    record = USStockEarnings(
                        symbol=symbol,
                        company_name=mapping.get("name", symbol),
                        report_date=trade_date,
                        after_hours_change_pct=change_pct,
                        a_share_impact=payload,
                    )
                    session.add(record)
        except Exception as e:
            logger.debug(f"美股 {symbol} 保存失败: {e}")

    # ============================================================
    # 财报/研报新闻自动抓取
    # ============================================================

    def _save_global_news_items(self, db_path: str, items: list[dict[str, Any]], log_label: str) -> int:
        """批量写入 global_news。"""
        if not items:
            return 0
        try:
            with get_db_session(db_path) as session:
                for item in items:
                    session.add(
                        GlobalNews(
                            source=item["source"],
                            title=item["title"][:500],
                            content=(item.get("content") or "")[:2000],
                            category=item.get("category"),
                            news_time=item.get("news_time") or datetime.now(),
                            importance=int(item.get("importance", 6)),
                            url=item.get("url", ""),
                        )
                    )
            logger.info(f"{log_label}保存: {len(items)} 条")
            return len(items)
        except Exception as e:
            logger.error(f"{log_label}保存失败: {e}")
            return 0

    def _collect_us_24h_hot_and_estimate(self, db_path: str) -> tuple[int, int]:
        """采集24小时热点美股与估计（来源：新浪实时行情）。"""
        quotes = self._fetch_sina_quotes([s.lower() for s in self.US_A_MAPPING])
        if not quotes:
            return 0, 0

        hot_candidates: list[dict[str, Any]] = []
        estimate_candidates: list[dict[str, Any]] = []
        for symbol, mapping in self.US_A_MAPPING.items():
            q = quotes.get(symbol.lower()) or {}
            price = q.get("price")
            change_pct = q.get("change_pct")
            if price is None or change_pct is None:
                continue

            direction = "偏多" if change_pct > 0 else "偏空" if change_pct < 0 else "中性"
            base = {
                "symbol": symbol,
                "name": mapping.get("name", symbol),
                "price": price,
                "change_pct": change_pct,
                "a_sectors": "、".join(mapping.get("a_share_sectors", [])[:3]),
            }
            estimate_candidates.append(base)
            if abs(change_pct) >= HOT_CHANGE_ALERT_PCT:
                hot_candidates.append(base)

        # 24h热点：按绝对涨跌幅排序
        hot_items = [{
                "source": "sina_us_24h",
                "title": f"24h热点美股: {row['symbol']}({row['name']}) {row['change_pct']:+.2f}%",
                "content": (
                    f"{row['symbol']} 最新价 {row['price']:.2f}，24小时变动 {row['change_pct']:+.2f}% ，"
                    f"关联A股方向：{row['a_sectors'] or '待补充'}"
                ),
                "category": "美股24h热点",
                "news_time": datetime.now(),
                "importance": 7 if abs(row["change_pct"]) >= HOT_IMPORTANCE_STRONG_PCT else 6,
                "url": f"https://finance.sina.com.cn/stock/usstock/c/{row['symbol'].lower()}.shtml",
            } for row in sorted(hot_candidates, key=lambda x: abs(x["change_pct"]), reverse=True)[:15]]

        # 美股估计：按涨跌幅排序，输出前20
        estimate_items = []
        for row in sorted(estimate_candidates, key=lambda x: abs(x["change_pct"]), reverse=True)[:20]:
            direction = "偏多" if row["change_pct"] > 0 else "偏空" if row["change_pct"] < 0 else "中性"
            estimate_items.append({
                "source": "sina_us_estimate",
                "title": f"美股估计: {row['symbol']} 当前市场预估{direction}",
                "content": (
                    f"{row['symbol']} 最新价 {row['price']:.2f}，涨跌 {row['change_pct']:+.2f}%。"
                    f"根据盘面波动给出短线预估：{direction}。关联A股：{row['a_sectors'] or '待补充'}"
                ),
                "category": "美股估计",
                "news_time": datetime.now(),
                "importance": 6,
                "url": f"https://finance.sina.com.cn/stock/usstock/c/{row['symbol'].lower()}.shtml",
            })

        return (
            self._save_global_news_items(db_path, hot_items, "美股24h热点"),
            self._save_global_news_items(db_path, estimate_items, "美股估计"),
        )

    def _collect_earnings_news(self, db_path: str) -> int:
        """Collect US earnings-related news into GlobalNews and return inserted count."""
        results: list[dict[str, Any]] = []

        category_earnings = "美股财报"
        cn_title = "标题"
        cn_digest = "摘要"
        cn_time = "发布时间"
        cn_url = "链接"

        tracked_names = {v["name"] for v in self.US_A_MAPPING.values()}
        tracked_names.update(self.US_A_MAPPING)
        us_keywords = tracked_names | {
            "美股", "纳斯达克", "道琼斯", "标普", "中概股", "华尔街",
            "英伟达", "特斯拉", "苹果", "微软", "谷歌", "亚马逊", "Meta",
        }
        earnings_keywords = {
            "财报", "业绩", "营收", "EPS", "指引", "超预期", "不及预期"
        }

        def _find_col(columns: list[str], aliases: list[str]) -> str | None:
            lowered = [(c, c.lower()) for c in columns]
            for alias in aliases:
                a = alias.lower()
                for raw, low in lowered:
                    if a in low:
                        return raw
            return None

        # Source 1: EastMoney global feed (via AKShare)
        try:
            df = ak.stock_info_global_em()
            if df is not None and not df.empty:
                cols = [str(c) for c in df.columns]
                title_col = _find_col(cols, ["title", cn_title])
                digest_col = _find_col(cols, ["summary", "digest", cn_digest, "content"])
                time_col = _find_col(cols, ["publish", "time", cn_time])
                url_col = _find_col(cols, ["url", "link", cn_url])

                cutoff = datetime.now() - timedelta(hours=24)
                for _, row in df.head(300).iterrows():
                    title = str(row.get(title_col, "") if title_col else "")
                    digest = str(row.get(digest_col, "") if digest_col else "")
                    if not digest:
                        digest = title

                    news_time = None
                    if time_col:
                        t = str(row.get(time_col, "") or "").strip()
                        if t:
                            try:
                                news_time = datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S")
                            except Exception:
                                news_time = None
                    if news_time and news_time < cutoff:
                        continue

                    text = f"{title} {digest}"
                    if not any(kw in text for kw in us_keywords):
                        continue
                    if not any(kw in text for kw in earnings_keywords):
                        continue

                    results.append({
                        "source": "eastmoney_us_earnings",
                        "title": title[:300],
                        "content": digest[:500],
                        "category": category_earnings,
                        "news_time": news_time or datetime.now(),
                        "importance": 8,
                        "url": str(row.get(url_col, "") if url_col else ""),
                    })
        except Exception as e:
            logger.debug(f"eastmoney us earnings fetch failed: {e}")

        # Source 2: WallstreetCN channel
        try:
            import requests as _req

            url = "https://api-one-wscn.awtmt.com/apiv1/content/articles"
            params = {"channel_id": "10", "limit": "40"}
            headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://wallstreetcn.com/"}
            resp = _req.get(url, params=params, headers=headers, timeout=12)
            if resp.status_code == HTTP_OK_STATUS:
                data = resp.json()
                payload = data.get("data", {}) if isinstance(data, dict) else {}
                items = payload.get("items", []) if isinstance(payload, dict) else []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title", "") or "")
                    summary = str(item.get("summary", "") or title)
                    text = f"{title} {summary}"
                    if not any(kw in text for kw in us_keywords):
                        continue
                    if not any(kw in text for kw in earnings_keywords):
                        continue

                    item_uri = item.get("uri") or item.get("id") or ""
                    news_url = (
                        f"https://wallstreetcn.com/articles/{item_uri}"
                        if item_uri else "https://wallstreetcn.com/"
                    )
                    results.append({
                        "source": "wallstreetcn_us_earnings",
                        "title": title[:300],
                        "content": summary[:500],
                        "category": category_earnings,
                        "news_time": datetime.now(),
                        "importance": 8,
                        "url": news_url,
                    })
        except Exception as e:
            logger.debug(f"wallstreetcn us earnings fetch failed: {e}")

        seen = set()
        unique = []
        for r in results:
            key = (r.get("source", ""), r.get("title", "")[:120])
            if key in seen:
                continue
            seen.add(key)
            unique.append(r)

        earnings_items = [r for r in unique if r.get("category") == category_earnings]

        # Fallback: synthesize one minimum item from intraday move
        if not earnings_items:
            quotes = self._fetch_sina_quotes([s.lower() for s in self.US_A_MAPPING])
            ranked = []
            for symbol, mapping in self.US_A_MAPPING.items():
                q = quotes.get(symbol.lower()) or {}
                pct = q.get("change_pct")
                price = q.get("price")
                if pct is None or price is None:
                    continue
                ranked.append((abs(float(pct)), symbol, mapping, float(price), float(pct)))
            ranked.sort(reverse=True, key=lambda x: x[0])
            if ranked:
                _, symbol, mapping, price, pct = ranked[0]
                earnings_items.append({
                    "source": "sina_us_earnings_est",
                    "title": f"财报预期跟踪: {symbol}({mapping.get('name', symbol)}) 波动 {pct:+.2f}%",
                    "content": (
                        f"{symbol} 最新价 {price:.2f}，日内变动 {pct:+.2f}%。"
                        "建议关注最近财报窗口、指引变化及业绩预期差。"
                    ),
                    "category": category_earnings,
                    "news_time": datetime.now(),
                    "importance": 6,
                    "url": f"https://finance.sina.com.cn/stock/usstock/c/{symbol.lower()}.shtml",
                })

        return self._save_global_news_items(db_path, earnings_items, "美股财报")
    @classmethod
    def get_mapping(cls) -> dict:
        """获取美股-A股映射表"""
        return cls.US_A_MAPPING


def _safe_float(val) -> float | None:
    """安全转换为 float"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
