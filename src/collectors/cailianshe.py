"""
财联社快讯采集器
数据来源：财联社电报 / 快讯页面
这是最重要的财经实时信息源

level 字段含义：
  A = 红色重大新闻（最重要）
  B = 重要新闻（加粗/高亮）
  C = 普通快讯
"""

import re
from contextlib import suppress
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup
from loguru import logger

from src.collectors.base import BaseCollector

HTTP_OK_STATUS = 200


class CailiansheCollector(BaseCollector):
    """财联社快讯采集器"""

    SOURCE_NAME = "cailianshe"

    # 财联社电报 API（可用端点）
    TELEGRAPH_LIST_URL = "https://www.cls.cn/nodeapi/telegraphList"
    # 备用旧端点
    TELEGRAPH_URL = "https://www.cls.cn/nodeapi/updateTelegraph"
    # 网页版
    WEB_URL = "https://www.cls.cn/telegraph"

    # level 到重要性的映射
    LEVEL_MAP = {
        "A": "red",       # 红色 - 重大新闻
        "B": "important",  # 重要新闻
        "C": "normal",     # 普通快讯
    }

    def collect(self) -> list[dict[str, Any]]:
        """采集财联社快讯"""
        results = []

        # 方式1: telegraphList API（主力）
        results.extend(self._collect_telegraph_list())

        # 方式2: 旧 API
        if not results:
            results.extend(self._collect_telegraph_legacy())

        # 方式3: 网页提取
        if not results:
            results.extend(self._collect_from_web())

        return results

    def _collect_telegraph_list(self) -> list[dict[str, Any]]:
        """通过 telegraphList API 采集（推荐）"""
        results = []
        try:
            params = {
                "app": "CailianpressWeb",
                "os": "web",
                "rn": "100",
            }
            resp = self.fetch_url(self.TELEGRAPH_LIST_URL, params=params, max_retries=2)
            if resp and resp.status_code == HTTP_OK_STATUS:
                data = resp.json()
                items = data.get("data", {}).get("roll_data", [])
                for item in items:
                    parsed = self._parse_item(item)
                    if parsed:
                        results.append(parsed)
                logger.info(f"[cailianshe] telegraphList 采集 {len(results)} 条")
        except Exception as e:
            logger.error(f"财联社 telegraphList 采集失败: {e}")
        return results

    def _collect_telegraph_legacy(self) -> list[dict[str, Any]]:
        """通过旧版 API 采集"""
        results = []
        try:
            params = {
                "app": "CailianpressWeb",
                "os": "web",
                "sv": "8.4.6",
                "rn": "50",
            }
            resp = self.fetch_url(self.TELEGRAPH_URL, params=params, max_retries=1)
            if resp and resp.status_code == HTTP_OK_STATUS:
                data = resp.json()
                items = data.get("data", {}).get("roll_data", [])
                for item in items:
                    parsed = self._parse_item(item)
                    if parsed:
                        results.append(parsed)
        except Exception as e:
            logger.debug(f"财联社旧API采集失败: {e}")
        return results

    def _parse_item(self, item: dict) -> dict[str, Any] | None:
        """解析单条电报数据"""
        try:
            # 时间
            ctime = item.get("ctime")
            news_time = None
            if ctime:
                with suppress(ValueError, TypeError):
                    news_time = datetime.fromtimestamp(int(ctime))

            # 标题和内容
            title = item.get("title", "")
            content = item.get("content", "")
            brief = item.get("brief", "")
            if not title and content:
                # 清理 HTML
                clean = re.sub(r'<[^>]+>', '', content)
                title = clean[:150]
            if not content:
                content = brief

            # 清理内容中的 HTML 标签
            if content:
                content = re.sub(r'<[^>]+>', '', content)

            if not title:
                return None

            # 重要性级别
            level = item.get("level", "C")
            importance = self.LEVEL_MAP.get(level, "normal")
            bold = item.get("bold", 0)
            if bold == 1 and importance == "normal":
                importance = "important"

            # 标签/分类
            subjects = item.get("subjects", [])
            tags = ",".join([s.get("subject_name", "") for s in subjects]) if subjects else ""

            # 关联股票
            stock_list = item.get("stock_list", [])
            stocks = []
            if stock_list:
                stocks.extend({
                        "code": s.get("code", ""),
                        "name": s.get("name", ""),
                    } for s in stock_list)

            # 关联板块
            plate_list = item.get("plate_list", [])
            plates = []
            if plate_list:
                plates.extend(p.get("name", "") for p in plate_list)

            return {
                "title": title,
                "content": content,
                "news_time": news_time,
                "category": "快讯",
                "importance": importance,  # red / important / normal
                "level": level,            # A / B / C
                "tags": tags,
                "stocks": stocks,          # [{"code": "xxx", "name": "xxx"}]
                "plates": plates,          # ["板块名"]
                "url": f"https://www.cls.cn/detail/{item.get('id', '')}",
                "news_id": item.get("id", ""),
            }
        except Exception as e:
            logger.debug(f"财联社解析条目失败: {e}")
            return None

    def _collect_from_web(self) -> list[dict[str, Any]]:
        """备用方案 - 从网页 SSR 数据中提取（httpx → Playwright 兜底）"""
        results = []

        # 先尝试 Playwright 渲染（更稳定，能处理 JS 渲染的 SSR）
        try:
            from src.collectors.browser_client import get_browser_client
            html = get_browser_client().fetch_html(self.WEB_URL, timeout=20000)
            if html:
                results = self._parse_web_html(html)
                if results:
                    logger.info(f"[cailianshe] Playwright网页采集 {len(results)} 条")
                    return results
        except Exception as e:
            logger.debug(f"财联社Playwright采集失败: {e}")

        # 再尝试 httpx
        try:
            resp = self.fetch_url(self.WEB_URL, max_retries=2)
            if resp and resp.status_code == HTTP_OK_STATUS:
                results = self._parse_web_html(resp.text)
                if results:
                    logger.info(f"[cailianshe] httpx网页采集 {len(results)} 条")

        except Exception as e:
            logger.error(f"财联社网页采集失败: {e}")
        return results

    def _parse_web_html(self, html: str) -> list[dict[str, Any]]:
        """从网页 HTML 中提取快讯（SSR JSON → HTML 元素两级解析）"""
        results = []
        import json as _json

        # 先尝试从 SSR props 中提取 JSON 数据
        match = re.search(
            r'<script[^>]*>\s*\{"props":\s*(\{.*?"telegraphList".*?\})\s*</script>',
            html, re.DOTALL
        )
        if not match:
            soup = BeautifulSoup(html, "lxml")
            for script in soup.select("script:not([src])"):
                text = script.string or ""
                if "telegraphList" in text:
                    match = re.search(r'\{"props":\s*(\{.*\})\s*', text)
                    if match:
                        break

        if match:
            try:
                page_data = _json.loads(match.group(0) if match.group(0).startswith("{") else "{" + match.group(0))
                telegraph_list = (
                    page_data.get("props", {})
                    .get("initialState", {})
                    .get("telegraph", {})
                    .get("telegraphList", [])
                )
                for item in telegraph_list:
                    parsed = self._parse_item(item)
                    if parsed:
                        results.append(parsed)
            except _json.JSONDecodeError:
                pass

        # 兜底: 从 HTML 元素提取
        if not results:
            soup = BeautifulSoup(html, "lxml")
            for item in soup.select(".telegraph-content-box")[:50]:
                text_el = item.select_one(".telegraph-content-box__text")
                if text_el:
                    content = text_el.get_text(strip=True)
                    results.append({
                        "title": content[:150],
                        "content": content,
                        "news_time": None,
                        "category": "快讯",
                        "importance": "normal",
                        "level": "C",
                        "tags": "",
                        "stocks": [],
                        "plates": [],
                        "url": self.WEB_URL,
                        "news_id": "",
                    })
        return results
