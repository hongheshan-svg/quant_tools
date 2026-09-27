"""
今日头条热搜采集器
数据来源：头条热搜榜 API
"""

from typing import Any

from loguru import logger

from src.collectors.base import BaseCollector

HTTP_OK_STATUS = 200


class ToutiaoCollector(BaseCollector):
    """今日头条热搜采集器"""

    SOURCE_NAME = "toutiao"

    HOT_SEARCH_URL = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"

    def collect(self) -> list[dict[str, Any]]:
        """采集今日头条热搜"""
        results = []

        # 方式1: 直接请求（有时可用）
        try:
            resp = self.fetch_url(
                self.HOT_SEARCH_URL,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                },
            )
            if resp and resp.status_code == HTTP_OK_STATUS:
                data = resp.json()
                items = data.get("data", [])
                for idx, item in enumerate(items):
                    results.append({
                        "title": item.get("Title", ""),
                        "rank": idx + 1,
                        "hot_value": item.get("HotValue"),
                        "category": item.get("Category", ""),
                        "url": item.get("Url"),
                    })
                if results:
                    logger.info(f"[toutiao] 直接API采集 {len(results)} 条")
                    return results
        except Exception as e:
            logger.debug(f"头条热搜直接请求失败: {e}")

        # 方式2: Playwright 渲染（绕过 JS 检测）
        if not results:
            results.extend(self._collect_via_playwright())

        # 方式3: 备用聚合源
        if not results:
            results.extend(self._collect_from_backup())

        return results

    def _collect_via_playwright(self) -> list[dict[str, Any]]:
        """通过 Playwright 渲染页面获取头条热搜"""
        results = []
        try:
            from src.collectors.browser_client import get_browser_client
            data = get_browser_client().fetch_json(
                self.HOT_SEARCH_URL,
                referer="https://www.toutiao.com/",
                timeout=15000
            )
            if data:
                items = data.get("data", [])
                for idx, item in enumerate(items):
                    results.append({
                        "title": item.get("Title", ""),
                        "rank": idx + 1,
                        "hot_value": item.get("HotValue"),
                        "category": item.get("Category", ""),
                        "url": item.get("Url"),
                    })
                if results:
                    logger.info(f"[toutiao] Playwright采集 {len(results)} 条")
        except Exception as e:
            logger.debug(f"头条热搜Playwright失败: {e}")
        return results

    def _collect_from_backup(self) -> list[dict[str, Any]]:
        """备用采集方案（第三方聚合）"""
        results = []
        backup_urls = [
            "https://api.vvhan.com/api/hotlist/toutiao",
            "https://tenapi.cn/v2/toutiao",
        ]
        for backup_url in backup_urls:
            try:
                resp = self.fetch_url(backup_url, max_retries=1)
                if resp and resp.status_code == HTTP_OK_STATUS:
                    data = resp.json()
                    # vvhan 格式
                    items = data.get("data", [])
                    for idx, item in enumerate(items):
                        title = item.get("title", item.get("name", ""))
                        if title:
                            results.append({
                                "title": title,
                                "rank": idx + 1,
                                "hot_value": item.get("hot"),
                                "category": None,
                                "url": item.get("url"),
                            })
                    if results:
                        logger.info(f"[toutiao] 备用源采集 {len(results)} 条: {backup_url}")
                        break
            except Exception as e:
                logger.debug(f"头条热搜备用方案失败 {backup_url}: {e}")
        return results
