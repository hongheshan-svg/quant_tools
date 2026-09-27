"""
韭研公社数据采集器
采集涨停复盘、题材解读内容
多源策略: 韭研公社官网 → 东方财富涨停复盘 → 同花顺热点
"""

from typing import Any

from bs4 import BeautifulSoup
from loguru import logger

from src.collectors.base import BaseCollector
from src.collectors.ths_client import TongHuaShunClient, get_ths_client

HTTP_OK_STATUS = 200
MIN_RESULTS_FALLBACK = 3
MIN_ARTICLE_TITLE_LEN = 5
MIN_STOCK_NAME_LEN = 2
MAX_STOCK_NAME_LEN = 8


class JiuyanCollector(BaseCollector):
    """韭研公社数据采集器（多源兜底）"""

    SOURCE_NAME = "jiuyan"

    BASE_URL = "https://www.jiuyangongshe.com"

    # 多个频道页面
    PAGES = [
        "/study_publish",   # 最新发布
        "/study_hot",       # 最新热度
    ]

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    def collect(self) -> list[dict[str, Any]]:
        """采集韭研公社涨停复盘等内容（多源兜底）"""
        results = []

        # === 源1: 韭研公社官网 ===
        results.extend(self._collect_from_homepage())
        for page in self.PAGES:
            results.extend(self._collect_page(page))

        # === 源2: 东方财富涨停复盘（兜底） ===
        if len(results) < MIN_RESULTS_FALLBACK:
            logger.info("韭研公社数据不足，启用东方财富涨停复盘兜底")
            results.extend(self._collect_eastmoney_limitup_review())

        # === 源3: 同花顺热股（兜底） ===
        if len(results) < MIN_RESULTS_FALLBACK:
            logger.info("数据仍不足，启用同花顺热股兜底")
            results.extend(self._collect_ths_hot())

        # 去重
        seen = set()
        unique = []
        for item in results:
            key = item["title"][:50]
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    def _collect_from_homepage(self) -> list[dict[str, Any]]:
        """从首页采集文章列表"""
        results = []
        try:
            resp = self.fetch_url(self.BASE_URL, max_retries=2)
            if resp and resp.status_code == HTTP_OK_STATUS:
                results = self._parse_page(resp.text)
        except Exception as e:
            logger.warning(f"韭研公社首页采集失败: {e}")
        return results

    def _collect_page(self, page_path: str) -> list[dict[str, Any]]:
        """采集指定频道页"""
        results = []
        try:
            url = f"{self.BASE_URL}{page_path}"
            resp = self.fetch_url(url, max_retries=1)
            if resp and resp.status_code == HTTP_OK_STATUS:
                results = self._parse_page(resp.text)
        except Exception as e:
            logger.debug(f"韭研公社 {page_path} 采集失败: {e}")
        return results

    def _collect_eastmoney_limitup_review(self) -> list[dict[str, Any]]:
        """东方财富涨停复盘/热点题材（兜底源）"""
        results = []

        # 涨停池
        try:
            from datetime import date as _date

            from src.collectors.em_client import get_em_client
            df = get_em_client().stock_zt_pool_em(date=_date.today().strftime("%Y%m%d"))
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    name = str(row.get("名称", ""))
                    reason = str(row.get("所属行业", "") or "")
                    if name:
                        results.append({
                            "title": f"涨停复盘: {name} — {reason}",
                            "content": f"东方财富涨停复盘: {name}，涨停原因: {reason}",
                            "category": "涨停复盘",
                            "tags": name,
                            "url": "",
                            "related_stocks": [name],
                        })
        except Exception as e:
            logger.debug(f"东方财富涨停复盘兜底失败: {e}")

        # 热门概念板块
        try:
            from src.collectors.em_client import get_em_client
            df2 = get_em_client().stock_board_concept_name_em()
            if df2 is not None and not df2.empty:
                for _, row in df2.head(10).iterrows():
                    name = str(row.get("板块名称", ""))
                    change = self._safe_float(row.get("涨跌幅"))
                    if name and change is not None:
                        results.append({
                            "title": f"热门概念: {name} 涨幅{change:+.2f}%",
                            "content": f"东方财富热门概念板块: {name}，涨幅{change:+.2f}%",
                            "category": "题材解读",
                            "tags": name,
                            "url": "",
                            "related_stocks": [],
                        })
        except Exception as e:
            logger.debug(f"东方财富热门概念兜底失败: {e}")

        return results

    def _collect_ths_hot(self) -> list[dict[str, Any]]:
        """同花顺热股/热点（第三兜底源）"""
        results = []
        try:
            stock_list = get_ths_client().fetch_hot_stocks(
                stock_type="a",
                hot_type="hour",
                list_type="normal",
            )
            for item in stock_list:
                name = item.get("name", "")
                code = item.get("code", "")
                tag = TongHuaShunClient._flatten_tag(item.get("tag", ""))
                if name:
                    results.append({
                        "title": f"同花顺热股: {name}({code}) {tag}",
                        "content": f"同花顺热度排行: {name}({code})，标签: {tag}",
                        "category": "热门个股",
                        "tags": name,
                        "url": "",
                        "related_stocks": [name],
                    })
        except Exception as e:
            logger.debug(f"同花顺热股兜底失败: {e}")
        return results

    def _parse_page(self, html: str) -> list[dict[str, Any]]:
        """解析页面 HTML 提取文章"""
        results = []
        try:
            soup = BeautifulSoup(html, "lxml")

            # 方式1: 提取文章链接 (href 含 /a/)
            article_links = soup.select("a[href*='/a/']")
            for link in article_links:
                title = link.get_text(strip=True)
                href = link.get("href", "")

                if title and len(title) > MIN_ARTICLE_TITLE_LEN:
                    if not href.startswith("http"):
                        href = f"{self.BASE_URL}{href}"

                    # 提取关联的股票名称（文章旁边可能有股票标签）
                    stocks = []
                    parent = link.parent
                    if parent:
                        stock_links = parent.select("a[href*='/search/']")
                        for sl in stock_links:
                            sname = sl.get_text(strip=True)
                            if sname and len(sname) <= MAX_STOCK_NAME_LEN:
                                stocks.append(sname)

                    results.append({
                        "title": title[:300],
                        "content": title[:500],
                        "category": self._classify_article(title),
                        "tags": ",".join(stocks) if stocks else "",
                        "url": href,
                        "related_stocks": stocks,
                    })

            # 方式2: 提取 .item 中的股票信息
            stock_items = soup.select(".item")
            for item in stock_items:
                name = item.get_text(strip=True)
                if name and MIN_STOCK_NAME_LEN <= len(name) <= MAX_STOCK_NAME_LEN:
                    # 这些是热门股票标签
                    results.append({
                        "title": f"韭研热股: {name}",
                        "content": f"韭研公社热门讨论股票: {name}",
                        "category": "热门个股",
                        "tags": name,
                        "url": "",
                        "related_stocks": [name],
                    })

        except Exception as e:
            logger.error(f"韭研公社页面解析失败: {e}")

        return results

    @staticmethod
    def _classify_article(title: str) -> str:
        """根据标题分类文章"""
        if any(kw in title for kw in ["涨停", "涨停板", "复盘", "封板", "涨幅"]):
            return "涨停复盘"
        if any(kw in title for kw in ["题材", "概念", "板块", "赛道", "产业链"]):
            return "题材解读"
        if any(kw in title for kw in ["龙头", "妖股", "连板", "龙虎榜"]):
            return "龙头分析"
        if any(kw in title for kw in ["研报", "研究", "分析", "深度", "估值"]):
            return "研报"
        if any(kw in title for kw in ["AI", "人工智能", "芯片", "半导体", "光伏", "新能源"]):
            return "题材解读"
        if any(kw in title for kw in ["推荐", "强烈", "看好", "机会"]):
            return "研报"
        return "其他"
