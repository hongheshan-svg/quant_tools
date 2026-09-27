"""
同花顺 API 客户端（Playwright）
统一热股/概念接口请求，提升稳定性。
"""

from __future__ import annotations

import re
import threading
import time
from urllib.parse import urlencode

from loguru import logger

from src.collectors.browser_client import get_browser_client


class TongHuaShunClient:
    """同花顺数据客户端。"""

    BASE_URL = "https://dq.10jqka.com.cn"
    BASE_REFERER = "https://www.10jqka.com.cn/"
    HOT_STOCK_PATH = "/fuyao/hot_list_data/out/hot_list/v1/stock"
    HOT_CONCEPT_PATH = "/fuyao/hot_list_data/out/hot_list/v1/concept"

    # 新鲜缓存与陈旧缓存：前者减少高频请求，后者在源抖动时保底输出
    CACHE_TTL_SECONDS = 90
    CACHE_MAX_STALE_SECONDS = 900
    MIN_CONCEPT_NAME_LENGTH = 2

    def __init__(self):
        self._cache: dict[str, tuple[float, dict]] = {}
        self._cache_lock = threading.RLock()

    @staticmethod
    def _is_valid(data: dict | None) -> bool:
        if not isinstance(data, dict):
            return False
        return isinstance(data.get("data"), dict)

    @staticmethod
    def _cache_key(path: str, params: dict | None) -> str:
        q = urlencode(sorted((params or {}).items()))
        return f"{path}?{q}" if q else path

    def _cache_set(self, key: str, data: dict):
        with self._cache_lock:
            self._cache[key] = (time.time(), data)

    def _cache_get(self, key: str, max_age: int) -> dict | None:
        with self._cache_lock:
            hit = self._cache.get(key)
        if not hit:
            return None
        ts, data = hit
        if time.time() - ts > max_age:
            return None
        return data

    def _request_live(
        self,
        path: str,
        params: dict | None = None,
        timeout: int = 12000,
        retries: int = 2,
    ) -> dict | None:
        url = f"{self.BASE_URL}{path}"
        params = params or {}
        client = get_browser_client()

        # 方式1：直接带 Referer 请求
        for attempt in range(retries):
            data = client.fetch_json(
                url=url,
                params=params,
                timeout=timeout,
                referer=self.BASE_REFERER,
            )
            if self._is_valid(data):
                return data
            logger.debug(f"同花顺请求重试[{attempt + 1}/{retries}] {path}")
            if attempt + 1 < retries:
                time.sleep(0.35 * (attempt + 1))

        # 方式2：先预热站点 cookie 再请求
        try:
            query = urlencode(params)
            full_url = f"{url}?{query}" if query else url
            data = client.fetch_with_cookies(
                url=full_url,
                init_url=self.BASE_REFERER,
                timeout=timeout,
            )
            if self._is_valid(data):
                return data
        except Exception as e:
            logger.debug(f"同花顺 cookie 请求失败: {e}")
        return None

    def request_json(
        self,
        path: str,
        params: dict | None = None,
        timeout: int = 12000,
        retries: int = 2,
    ) -> dict | None:
        """请求同花顺 JSON 接口，失败时自动重试并走 cookie 预热。"""
        params = params or {}
        ckey = self._cache_key(path, params)

        fresh = self._cache_get(ckey, self.CACHE_TTL_SECONDS)
        if self._is_valid(fresh):
            return fresh

        data = self._request_live(path=path, params=params, timeout=timeout, retries=retries)
        if self._is_valid(data):
            self._cache_set(ckey, data)
            return data

        stale = self._cache_get(ckey, self.CACHE_MAX_STALE_SECONDS)
        if self._is_valid(stale):
            logger.warning(f"同花顺接口波动，回退使用缓存数据: {path}")
            return stale
        return None

    @staticmethod
    def _flatten_tag(tag_obj) -> str:
        if isinstance(tag_obj, dict):
            concept_tag = tag_obj.get("concept_tag", [])
            popularity_tag = tag_obj.get("popularity_tag", "")
            if isinstance(concept_tag, list):
                concept_text = "/".join(str(x) for x in concept_tag if x)
            else:
                concept_text = str(concept_tag or "")
            return " | ".join([x for x in [concept_text, str(popularity_tag or "")] if x])
        if isinstance(tag_obj, list):
            return "/".join(str(x) for x in tag_obj if x)
        return str(tag_obj or "")

    def fetch_hot_stocks(
        self,
        stock_type: str = "a",
        hot_type: str = "hour",
        list_type: str = "normal",
    ) -> list[dict]:
        """获取同花顺热股列表。"""
        candidates = [
            {"stock_type": stock_type, "type": hot_type, "list_type": list_type},
            {"stock_type": stock_type, "type": "day", "list_type": "normal"},
            {"stock_type": "a", "type": "day", "list_type": "normal"},
        ]
        seen = set()
        for params in candidates:
            pkey = tuple(sorted(params.items()))
            if pkey in seen:
                continue
            seen.add(pkey)
            data = self.request_json(self.HOT_STOCK_PATH, params=params)
            if not data:
                continue
            stock_list = (data.get("data", {}) or {}).get("stock_list", []) or []
            if stock_list:
                return stock_list
        return []

    def fetch_hot_concepts(
        self,
        stock_type: str = "a",
        hot_type: str = "day",
        list_type: str = "normal",
    ) -> list[dict]:
        """获取同花顺热门概念列表。"""
        candidates = [
            {"stock_type": stock_type, "type": hot_type, "list_type": list_type},
            {"stock_type": stock_type, "type": "hour", "list_type": "normal"},
            {"stock_type": "a", "type": "day", "list_type": "normal"},
        ]
        seen = set()
        for params in candidates:
            pkey = tuple(sorted(params.items()))
            if pkey in seen:
                continue
            seen.add(pkey)
            data = self.request_json(self.HOT_CONCEPT_PATH, params=params)
            if not data:
                continue
            payload = (data.get("data", {}) or {})
            concept_list = payload.get("stock_list") or payload.get("concept_list") or []
            if concept_list:
                return concept_list

        # 同花顺概念接口偶发 404，回退到热股标签聚合，保证稳定输出
        stock_list = self.fetch_hot_stocks(
            stock_type=stock_type,
            hot_type=hot_type,
            list_type=list_type,
        )
        counter: dict[str, int] = {}
        for item in stock_list:
            raw_tag = item.get("tag")
            concept_values: list[str] = []
            if isinstance(raw_tag, dict):
                concept_raw = raw_tag.get("concept_tag")
                if isinstance(concept_raw, list):
                    concept_values.extend(str(x) for x in concept_raw if x)
                elif isinstance(concept_raw, str):
                    concept_values.append(concept_raw)
            elif isinstance(raw_tag, list):
                concept_values.extend(str(x) for x in raw_tag if x)
            elif isinstance(raw_tag, str):
                concept_values.extend(re.split(r"[,\|/、，\s]+", raw_tag))

            for tag in concept_values:
                concept = tag.strip()
                if len(concept) < self.MIN_CONCEPT_NAME_LENGTH:
                    continue
                counter[concept] = counter.get(concept, 0) + 1
        ranked = sorted(counter.items(), key=lambda x: (-x[1], x[0]))[:30]
        return [{"name": name, "hot_count": cnt} for name, cnt in ranked]


_ths_client: TongHuaShunClient | None = None


def get_ths_client() -> TongHuaShunClient:
    """获取同花顺客户端单例。"""
    global _ths_client
    if _ths_client is None:
        _ths_client = TongHuaShunClient()
    return _ths_client
