"""
多源热点采集器：同花顺热股 + 东方财富热门概念/涨停复盘
独立于韭研公社采集器，定时并行采集，结果存入 FinanceNews 表。
"""

from typing import Any

from loguru import logger

from src.collectors.base import BaseCollector
from src.collectors.em_client import get_em_client
from src.collectors.ths_client import TongHuaShunClient, get_ths_client


class HotTopicCollector(BaseCollector):
    """多源热点采集器：同花顺 + 东方财富"""

    SOURCE_NAME = "hot_topics"

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    def collect(self) -> list[dict[str, Any]]:
        """采集所有热点数据源，返回统一结构。"""
        results: list[dict[str, Any]] = []

        ths = self._collect_ths_hot_stocks()
        results.extend(ths)
        logger.info(f"同花顺热股采集: {len(ths)} 条")

        em_concept = self._collect_eastmoney_hot_concept()
        results.extend(em_concept)
        logger.info(f"东方财富热门概念采集: {len(em_concept)} 条")

        em_limitup = self._collect_eastmoney_limitup_review()
        results.extend(em_limitup)
        logger.info(f"东方财富涨停复盘采集: {len(em_limitup)} 条")

        logger.info(f"热点采集汇总: 共 {len(results)} 条")
        return results

    def _collect_ths_hot_stocks(self) -> list[dict[str, Any]]:
        """同花顺热股排行（独立采集源）。"""
        results = []
        try:
            stock_list = get_ths_client().fetch_hot_stocks(
                stock_type="a",
                hot_type="hour",
                list_type="normal",
            )
            for i, item in enumerate(stock_list, start=1):
                name = item.get("name", "")
                code = item.get("code", "")
                tag = TongHuaShunClient._flatten_tag(item.get("tag", ""))
                hot_rank = item.get("order", i)
                if name:
                    results.append({
                        "source": "ths_hot",
                        "title": f"同花顺热股#{hot_rank}: {name}({code}) {tag}",
                        "content": f"同花顺热度排行第{hot_rank}: {name}({code})，标签: {tag}",
                        "category": "热门个股",
                        "tags": f"{name},{code}",
                        "url": f"https://stockpage.10jqka.com.cn/{code}/",
                        "related_stocks": [name],
                    })
        except Exception as e:
            logger.warning(f"同花顺热股采集失败: {e}")
        return results

    def _collect_eastmoney_hot_concept(self) -> list[dict[str, Any]]:
        """东方财富热门概念板块（领涨概念），push2接口 + AKShare兜底。"""
        results = []
        # 方式1: Playwright + push2 API
        try:
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            params = {
                "pn": "1", "pz": "20", "po": "1",
                "np": "1", "fltt": "2", "invt": "2",
                "fid": "f3",
                "fs": "m:90+t:3+f:!50",
                "fields": "f2,f3,f8,f12,f14",
            }
            data = get_em_client().request_json(
                url,
                params=params,
                timeout=8000,
                referer="https://quote.eastmoney.com/",
            )
            if data:
                for i, item in enumerate((data.get("data", {}) or {}).get("diff", []) or []):
                    name = item.get("f14", "")
                    change_pct = self._safe_float(item.get("f3"))
                    turnover = self._safe_float(item.get("f8"))
                    if name and change_pct is not None:
                        results.append({
                            "source": "eastmoney_hot",
                            "title": f"热门概念#{i+1}: {name} 涨幅{change_pct:+.2f}%",
                            "content": f"东方财富热门概念板块: {name}，涨幅{change_pct:+.2f}%，换手率{(turnover if turnover is not None else 0):.2f}%",
                            "category": "热门概念",
                            "tags": name,
                            "url": "",
                            "related_stocks": [],
                        })
        except Exception as e:
            logger.debug(f"东方财富push2热门概念失败: {e}")

        # 方式2: Playwright 兜底
        if not results:
            try:
                df = get_em_client().stock_board_concept_name_em()
                if df is not None and not df.empty:
                    if "涨跌幅" in df.columns:
                        df = df.sort_values("涨跌幅", ascending=False).head(20)
                    for i, (_, row) in enumerate(df.iterrows()):
                        name = str(row.get("板块名称", ""))
                        change_pct = self._safe_float(row.get("涨跌幅"))
                        if name:
                            results.append({
                                "source": "eastmoney_hot",
                                "title": f"热门概念#{i+1}: {name} 涨幅{(change_pct if change_pct is not None else 0):+.2f}%",
                                "content": f"热门概念板块: {name}，涨幅{(change_pct if change_pct is not None else 0):+.2f}%",
                                "category": "热门概念",
                                "tags": name,
                                "url": "",
                                "related_stocks": [],
                            })
                    logger.info(f"AKShare热门概念兜底: {len(results)} 条")
            except Exception as e:
                logger.debug(f"AKShare热门概念也失败: {e}")

        # 方式3: 同花顺概念板块 (第三道防线)
        if not results:
            try:
                concept_list = get_ths_client().fetch_hot_concepts(
                    stock_type="a",
                    hot_type="day",
                    list_type="normal",
                )
                for i, item in enumerate(concept_list):
                    name = item.get("name", "")
                    if name:
                        results.append({
                            "source": "eastmoney_hot",
                            "title": f"热门概念#{i+1}: {name}",
                            "content": f"同花顺热门概念: {name}",
                            "category": "热门概念",
                            "tags": name,
                            "url": "https://www.10jqka.com.cn",
                            "related_stocks": [],
                        })
                if results:
                    logger.info(f"同花顺概念板块兜底: {len(results)} 条")
            except Exception as e:
                logger.warning(f"同花顺概念板块也失败: {e}")

        return results

    def _collect_eastmoney_limitup_review(self) -> list[dict[str, Any]]:
        """东方财富涨停复盘/涨停原因，push2ex + AKShare兜底。"""
        results = []
        # 方式1: Playwright + push2ex API
        try:
            from datetime import date as _date
            url = "https://push2ex.eastmoney.com/getTopicZTPool"
            params = {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Ession": "1",
                "sort": "fbt:asc",
                "date": _date.today().strftime("%Y%m%d"),
                "_": str(int(__import__("time").time() * 1000)),
            }
            data = get_em_client().request_json(
                url,
                params=params,
                timeout=8000,
                referer="https://quote.eastmoney.com/",
            )
            if data:
                for item in (data.get("data", {}) or {}).get("pool", []) or []:
                    name = item.get("n", "")
                    reason = item.get("zdp", "") or item.get("hybk", "")
                    code = item.get("c", "")
                    if name:
                        results.append({
                            "source": "eastmoney_hot",
                            "title": f"涨停复盘: {name}({code}) — {reason}",
                            "content": f"东方财富涨停复盘: {name}({code})，涨停原因: {reason}",
                            "category": "涨停复盘",
                            "tags": f"{name},{code},{reason}",
                            "url": "",
                            "related_stocks": [name],
                        })
        except Exception as e:
            logger.debug(f"东方财富push2ex涨停复盘失败: {e}")

        # 方式2: Playwright 涨停池兜底
        if not results:
            try:
                from datetime import date as _date
                df = get_em_client().stock_zt_pool_em(date=_date.today().strftime("%Y%m%d"))
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        name = str(row.get("名称", ""))
                        code = str(row.get("代码", ""))
                        reason = str(row.get("涨停原因", "") or "")
                        if name:
                            results.append({
                                "source": "eastmoney_hot",
                                "title": f"涨停复盘: {name}({code}) — {reason}",
                                "content": f"涨停复盘: {name}({code})，涨停原因: {reason}",
                                "category": "涨停复盘",
                                "tags": f"{name},{code},{reason}",
                                "url": "",
                                "related_stocks": [name],
                            })
                    logger.info(f"AKShare涨停池兜底: {len(results)} 条")
            except Exception as e:
                logger.warning(f"AKShare涨停池也失败: {e}")

        return results
