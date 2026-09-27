"""
抖音热搜采集器
数据来源：抖音热搜榜 API
"""

from typing import Any

from loguru import logger

from src.collectors.base import BaseCollector

HTTP_OK_STATUS = 200


class DouyinCollector(BaseCollector):
    """抖音热搜采集器"""

    SOURCE_NAME = "douyin"

    # 抖音热搜 API
    HOT_SEARCH_URL = "https://www.douyin.com/aweme/v1/web/hot/search/list/"

    def collect(self) -> list[dict[str, Any]]:
        """采集抖音热搜榜"""
        results = []

        try:
            resp = self.fetch_url(
                self.HOT_SEARCH_URL,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://www.douyin.com/",
                },
            )
            if resp and resp.status_code == HTTP_OK_STATUS:
                data = resp.json()
                word_list = data.get("data", {}).get("word_list", [])
                for idx, item in enumerate(word_list):
                    results.append({
                        "title": item.get("word", ""),
                        "rank": idx + 1,
                        "hot_value": item.get("hot_value"),
                        "category": item.get("word_type", ""),
                        "url": None,
                    })
        except Exception as e:
            logger.error(f"抖音热搜采集失败: {e}")

        # 备用方案: 通过第三方API
        if not results:
            results = self._collect_from_backup()

        return results

    def _collect_from_backup(self) -> list[dict[str, Any]]:
        """备用采集方案 - 通过公共热搜API聚合"""
        results = []
        try:
            # 尝试 tophub 等聚合API
            backup_url = "https://api.vvhan.com/api/hotlist/douyinHot"
            resp = self.fetch_url(backup_url)
            if resp and resp.status_code == HTTP_OK_STATUS:
                data = resp.json()
                items = data.get("data", [])
                for idx, item in enumerate(items):
                    results.append({
                        "title": item.get("title", ""),
                        "rank": idx + 1,
                        "hot_value": item.get("hot", None),
                        "category": None,
                        "url": item.get("url"),
                    })
        except Exception as e:
            logger.debug(f"抖音热搜备用方案失败: {e}")
        return results
