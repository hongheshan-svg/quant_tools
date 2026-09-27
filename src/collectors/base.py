"""
数据采集器基类 - 统一接口、重试机制、日志记录
"""

import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
from loguru import logger


class BaseCollector(ABC):
    """数据采集器基类"""

    # 子类应覆盖
    SOURCE_NAME: str = "base"

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.http_client = httpx.Client(
            timeout=30,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
            follow_redirects=True,
        )

    @abstractmethod
    def collect(self) -> list[dict[str, Any]]:
        """
        执行数据采集，返回结构化数据列表

        Returns:
            list[dict]: 采集到的数据列表
        """
        ...

    def fetch_url(self, url: str, params: dict = None, max_retries: int = 3, **kwargs) -> httpx.Response | None:
        """
        带重试机制的 HTTP GET 请求

        Args:
            url: 请求URL
            params: 查询参数
            max_retries: 最大重试次数

        Returns:
            Response 对象，失败返回 None
        """
        for attempt in range(1, max_retries + 1):
            try:
                resp = self.http_client.get(url, params=params, **kwargs)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                logger.warning(
                    f"[{self.SOURCE_NAME}] HTTP {e.response.status_code} - "
                    f"URL: {url} (attempt {attempt}/{max_retries})"
                )
            except httpx.RequestError as e:
                logger.warning(
                    f"[{self.SOURCE_NAME}] 请求失败: {e} (attempt {attempt}/{max_retries})"
                )
            if attempt < max_retries:
                time.sleep(2 ** attempt)  # 指数退避

        logger.error(f"[{self.SOURCE_NAME}] 所有重试均失败: {url}")
        return None

    def post_url(self, url: str, data: dict = None, json_data: dict = None,
                 max_retries: int = 3, **kwargs) -> httpx.Response | None:
        """带重试机制的 HTTP POST 请求"""
        for attempt in range(1, max_retries + 1):
            try:
                resp = self.http_client.post(url, data=data, json=json_data, **kwargs)
                resp.raise_for_status()
                return resp
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                logger.warning(
                    f"[{self.SOURCE_NAME}] POST 失败: {e} (attempt {attempt}/{max_retries})"
                )
            if attempt < max_retries:
                time.sleep(2 ** attempt)

        logger.error(f"[{self.SOURCE_NAME}] POST 所有重试均失败: {url}")
        return None

    def safe_collect(self) -> list[dict[str, Any]]:
        """安全采集 - 捕获所有异常，确保不会崩溃"""
        try:
            data = self.collect()
            logger.info(f"[{self.SOURCE_NAME}] 采集完成, 获取 {len(data)} 条数据")
            return data
        except Exception as e:
            logger.error(f"[{self.SOURCE_NAME}] 采集异常: {e}")
            return []

    def close(self):
        """关闭 HTTP 客户端"""
        self.http_client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
