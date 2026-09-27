"""
微博热搜采集器
数据来源：微博热搜榜 API
"""

from typing import Any

from loguru import logger

from src.collectors.base import BaseCollector

HTTP_OK_STATUS = 200


class WeiboCollector(BaseCollector):
    """微博热搜采集器"""

    SOURCE_NAME = "weibo"

    HOT_SEARCH_URL = "https://weibo.com/ajax/side/hotSearch"

    def collect(self) -> list[dict[str, Any]]:
        """采集微博热搜榜"""
        results = []

        # 方式1: 直接请求微博 API
        try:
            resp = self.fetch_url(self.HOT_SEARCH_URL)
            if resp and resp.status_code == HTTP_OK_STATUS:
                data = resp.json()
                realtime = data.get("data", {}).get("realtime", [])
                for idx, item in enumerate(realtime):
                    results.append({
                        "title": item.get("word", ""),
                        "rank": idx + 1,
                        "hot_value": item.get("num"),
                        "category": item.get("category", ""),
                        "url": f"https://s.weibo.com/weibo?q=%23{item.get('word', '')}%23",
                    })
                if results:
                    logger.info(f"[weibo] 直接API采集 {len(results)} 条")
                    return results
        except Exception as e:
            logger.debug(f"微博热搜直接API失败: {e}")

        # 方式2: Playwright 浏览器（绕过 403）
        try:
            from src.collectors.browser_client import get_browser_client
            data = get_browser_client().fetch_json(
                self.HOT_SEARCH_URL,
                referer="https://weibo.com/",
                timeout=15000
            )
            if data:
                realtime = data.get("data", {}).get("realtime", [])
                for idx, item in enumerate(realtime):
                    results.append({
                        "title": item.get("word", ""),
                        "rank": idx + 1,
                        "hot_value": item.get("num"),
                        "category": item.get("category", ""),
                        "url": f"https://s.weibo.com/weibo?q=%23{item.get('word', '')}%23",
                    })
                if results:
                    logger.info(f"[weibo] Playwright采集 {len(results)} 条")
                    return results
        except Exception as e:
            logger.debug(f"微博热搜Playwright失败: {e}")

        # 方式3: AKShare
        try:
            import akshare as ak
            df = ak.weibo_search_list(symbol="实时热搜")
            if df is not None and not df.empty:
                for idx, row in df.iterrows():
                    results.append({
                        "title": str(row.get("关键词", row.iloc[0] if len(row) > 0 else "")),
                        "rank": idx + 1,
                        "hot_value": int(row.get("热度", 0)) if "热度" in row.index else None,
                        "category": str(row.get("分类", "")) if "分类" in row.index else None,
                        "url": None,
                    })
                if results:
                    logger.info(f"[weibo] AKShare采集 {len(results)} 条")
        except Exception as e:
            logger.debug(f"AKShare 微博热搜获取失败: {e}")

        return results
