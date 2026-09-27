"""
雪球数据采集器
采集个股讨论、研报摘要，针对涨停股票定向采集
"""

import re
from typing import Any

from bs4 import BeautifulSoup
from loguru import logger

from src.collectors.base import BaseCollector

HTTP_OK_STATUS = 200
HOT_POST_MIN_TEXT_LEN = 10
SYMBOL_WITH_PREFIX_LEN = 6
POST_MIN_TEXT_LEN = 20


class XueqiuCollector(BaseCollector):
    """雪球数据采集器"""

    SOURCE_NAME = "xueqiu"

    BASE_URL = "https://xueqiu.com"
    HOT_STOCKS_API = "https://stock.xueqiu.com/v5/stock/hot_stock/list.json"
    HOT_LIST_API = "https://xueqiu.com/service/v5/stock/hot_stock/list.json"
    STOCK_NEWS_URL = "https://xueqiu.com/statuses/stock_timeline.json"

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._init_cookies()

    def _init_cookies(self):
        """初始化雪球 cookie（访问首页获取）"""
        try:
            resp = self.fetch_url(self.BASE_URL, max_retries=1)
            if resp:
                logger.debug(f"[xueqiu] Cookie 初始化成功: {len(self.http_client.cookies)} cookies")
        except Exception:
            pass

    def collect(self) -> list[dict[str, Any]]:
        """采集雪球热门讨论"""
        results = []

        # 方式1: 直接 API（需要 cookie）
        results.extend(self._collect_hot_stocks_api())

        # 方式2: Playwright 带 cookie 请求
        if not results:
            results.extend(self._collect_via_playwright())

        # 方式3: 从首页 HTML 提取
        if not results:
            results.extend(self._collect_from_homepage())

        # 方式4: AKShare 热门股票排行兜底
        if not results:
            results.extend(self._collect_from_akshare())

        return results

    def collect_for_stocks(self, stock_codes: list[str]) -> list[dict[str, Any]]:
        """针对指定股票代码采集讨论和分析"""
        results = []
        for code in stock_codes:
            results.extend(self._collect_stock_posts(code))
        return results

    def _collect_hot_stocks_api(self) -> list[dict[str, Any]]:
        """通过 API 采集雪球热门股票"""
        results = []
        for api_url in [self.HOT_STOCKS_API, self.HOT_LIST_API]:
            try:
                params = {"size": 30, "type": "10", "_type": "10"}
                resp = self.fetch_url(api_url, params=params, max_retries=1)
                if resp and resp.status_code == HTTP_OK_STATUS:
                    try:
                        data = resp.json()
                    except Exception:
                        continue
                    items = data.get("data", {}).get("items", [])
                    for item in items:
                        code = item.get("code", "")
                        clean_code = code.replace("SH", "").replace("SZ", "")
                        results.append({
                            "title": f"雪球热议: {item.get('name', '')} ({clean_code})",
                            "content": f"关注度: {item.get('value', '')}, 讨论数: {item.get('count', '')}",
                            "stock_code": clean_code,
                            "stock_name": item.get("name", ""),
                            "category": "热门讨论",
                            "url": f"https://xueqiu.com/S/{code}",
                        })
                    if results:
                        logger.info(f"[xueqiu] 直接API采集 {len(results)} 条")
                        break
            except Exception as e:
                logger.debug(f"[xueqiu] API采集失败 {api_url}: {e}")
        return results

    def _collect_via_playwright(self) -> list[dict[str, Any]]:
        """通过 Playwright 携带 cookie 请求雪球 API"""
        results = []
        try:
            from src.collectors.browser_client import get_browser_client
            data = get_browser_client().fetch_with_cookies(
                url=self.HOT_STOCKS_API + "?size=30&type=10&_type=10",
                init_url=self.BASE_URL,
                timeout=20000
            )
            if data:
                items = data.get("data", {}).get("items", [])
                for item in items:
                    code = item.get("code", "")
                    clean_code = code.replace("SH", "").replace("SZ", "")
                    results.append({
                        "title": f"雪球热议: {item.get('name', '')} ({clean_code})",
                        "content": f"关注度: {item.get('value', '')}, 讨论数: {item.get('count', '')}",
                        "stock_code": clean_code,
                        "stock_name": item.get("name", ""),
                        "category": "热门讨论",
                        "url": f"https://xueqiu.com/S/{code}",
                    })
                if results:
                    logger.info(f"[xueqiu] Playwright采集 {len(results)} 条")
        except Exception as e:
            logger.debug(f"[xueqiu] Playwright采集失败: {e}")
        return results

    def _collect_from_homepage(self) -> list[dict[str, Any]]:
        """从雪球首页 HTML 提取热门股票和讨论"""
        results = []
        try:
            resp = self.fetch_url(self.BASE_URL, max_retries=2)
            if not resp or resp.status_code != HTTP_OK_STATUS:
                return results

            soup = BeautifulSoup(resp.text, "lxml")

            stock_links = soup.select("a[href*='/S/']")
            seen_codes = set()
            for link in stock_links:
                href = link.get("href", "")
                text = link.get_text(strip=True)
                match = re.search(r'/S/(SH|SZ)(\d{6})', href)
                if match and text:
                    prefix = match.group(1)
                    code = match.group(2)
                    if code not in seen_codes:
                        seen_codes.add(code)
                        results.append({
                            "title": f"雪球讨论: {text}",
                            "content": text,
                            "stock_code": code,
                            "stock_name": text,
                            "category": "首页热门",
                            "url": f"https://xueqiu.com/S/{prefix}{code}",
                        })

            post_links = soup.select("a[href*='/statuses/'], a[href*='xueqiu.com/']")
            for link in post_links:
                text = link.get_text(strip=True)
                href = link.get("href", "")
                if text and len(text) > HOT_POST_MIN_TEXT_LEN and "/S/" not in href:
                    if not href.startswith("http"):
                        href = f"https://xueqiu.com{href}"
                    results.append({
                        "title": text[:200],
                        "content": text[:500],
                        "stock_code": "",
                        "stock_name": "",
                        "category": "热门帖子",
                        "url": href,
                    })

            logger.info(f"[xueqiu] 从首页提取到 {len(results)} 条数据")
        except Exception as e:
            logger.error(f"[xueqiu] 首页采集失败: {e}")
        return results

    def _collect_from_akshare(self) -> list[dict[str, Any]]:
        """使用 AKShare 热门股票排行作为兜底数据源"""
        results = []
        try:
            import akshare as ak
            df = ak.stock_hot_rank_em()
            if df is not None and not df.empty:
                for _, row in df.head(30).iterrows():
                    symbol = str(row.get("代码", ""))
                    name = str(row.get("股票名称", ""))
                    rank = row.get("当前排名", "")
                    price = row.get("最新价", 0)
                    change_pct = self._safe_float(row.get("涨跌幅"))
                    code = re.sub(r"^[A-Za-z]{2}", "", symbol)
                    if not code or not code.isdigit():
                        continue
                    prefix = symbol[:2].upper() if len(symbol) > SYMBOL_WITH_PREFIX_LEN else ("SH" if code.startswith("6") else "SZ")
                    results.append({
                        "title": f"热股#{rank}: {name} ({code})",
                        "content": f"东方财富热股排行第{rank}名, 最新价{price}, 涨跌幅{(change_pct if change_pct is not None else 0):.2f}%",
                        "stock_code": code,
                        "stock_name": name,
                        "category": "热门股票",
                        "url": f"https://xueqiu.com/S/{prefix}{code}",
                    })
                logger.info(f"[xueqiu] AKShare 热股排行获取 {len(results)} 条")
        except Exception as e:
            logger.error(f"[xueqiu] AKShare 热股排行失败: {e}")
        return results

    def _collect_stock_posts(self, stock_code: str) -> list[dict[str, Any]]:
        """采集指定股票的讨论帖子"""
        results = []
        try:
            if stock_code.startswith("6"):
                symbol = f"SH{stock_code}"
            else:
                symbol = f"SZ{stock_code}"

            params = {
                "symbol_id": symbol,
                "count": 20,
                "comment": 0,
                "page": 1,
                "type": "",
            }
            resp = self.fetch_url(self.STOCK_NEWS_URL, params=params, max_retries=1)
            if resp and resp.status_code == HTTP_OK_STATUS:
                try:
                    data = resp.json()
                except Exception:
                    return results
                items = data.get("list", [])
                for item in items:
                    text = item.get("text", "")
                    clean_text = re.sub(r'<[^>]+>', '', text)
                    if len(clean_text) > POST_MIN_TEXT_LEN:
                        results.append({
                            "title": clean_text[:100],
                            "content": clean_text[:500],
                            "stock_code": stock_code,
                            "category": "个股讨论",
                            "url": f"https://xueqiu.com{item.get('target', '')}",
                        })
        except Exception as e:
            logger.debug(f"[xueqiu] 个股讨论采集失败 [{stock_code}]: {e}")
        return results
