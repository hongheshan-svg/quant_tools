"""
通用 Playwright 浏览器客户端
用于绕过反爬虫检测的社交媒体/新闻网站数据采集。

注意:
- Playwright 同步 API 相关对象不能跨线程复用。
- 客户端采用“每线程一个实例（thread-local）”。
"""

from __future__ import annotations

from contextlib import suppress
import json
import re
import threading

from loguru import logger
from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

HTTP_OK_STATUS = 200


class BrowserClient:
    """通用浏览器客户端（线程内复用浏览器实例）"""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context: BrowserContext | None = None
        self._browser_lock = threading.RLock()
        self._request_lock = threading.RLock()

    def _ensure_browser(self):
        """确保浏览器已启动"""
        with self._browser_lock:
            if self._browser is not None and self._context is not None:
                return
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )

    def _reset_browser(self):
        with self._browser_lock:
            try:
                if self._context:
                    self._context.close()
            except Exception:
                pass
            try:
                if self._browser:
                    self._browser.close()
            except Exception:
                pass
            try:
                if self._playwright:
                    self._playwright.stop()
            except Exception:
                pass
            self._browser = None
            self._context = None
            self._playwright = None

    @staticmethod
    def _try_parse_json(text: str):
        raw = (text or "").strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            pass
        # JSONP: callback({...})
        match = re.search(r"\((.+)\)\s*;?\s*$", raw, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except Exception:
            return None

    def get_page(self) -> Page:
        """获取一个新页面"""
        self._ensure_browser()
        assert self._context is not None
        return self._context.new_page()

    def fetch_json(
        self,
        url: str,
        params: dict | None = None,
        timeout: int = 15000,
        referer: str | None = None,
    ) -> dict | None:
        """
        使用浏览器 API 请求获取 JSON（带真实浏览器 TLS 指纹）
        适用于需要绕过反爬的 API 接口
        """
        try:
            with self._request_lock:
                self._ensure_browser()
                assert self._context is not None
                headers = {}
                if referer:
                    headers["Referer"] = referer
                resp = self._context.request.get(
                    url,
                    params=params or {},
                    headers=headers,
                    timeout=timeout,
                )
                if resp.status != HTTP_OK_STATUS:
                    logger.warning(f"Browser API 返回 {resp.status}: {url}")
                    return None
                try:
                    return resp.json()
                except Exception:
                    parsed = self._try_parse_json(resp.text())
                    if isinstance(parsed, dict):
                        return parsed
                    logger.warning(f"Browser API JSON 解析失败: {url}")
                    return None
        except Exception as e:
            logger.warning(f"Browser API 请求失败 {url}: {e}")
            self._reset_browser()
            return None

    def fetch_html(self, url: str, wait_selector: str = None, timeout: int = 15000) -> str | None:
        """
        使用浏览器渲染页面获取 HTML
        适用于需要 JavaScript 渲染的页面
        """
        page = None
        try:
            with self._request_lock:
                page = self.get_page()
                page.goto(url, timeout=timeout, wait_until="domcontentloaded")
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=5000)
                    except PlaywrightTimeout:
                        logger.debug(f"等待选择器超时: {wait_selector}")
                return page.content()
        except Exception as e:
            logger.warning(f"Browser 页面渲染失败 {url}: {e}")
            self._reset_browser()
            return None
        finally:
            if page:
                with suppress(Exception):
                    page.close()

    def fetch_with_cookies(
        self,
        url: str,
        init_url: str = None,
        wait_selector: str = None,
        timeout: int = 15000,
    ) -> dict | None:
        """
        先访问初始化页面获取 cookie，再请求目标 API
        适用于需要 session/cookie 的接口（如雪球/同花顺）
        """
        page = None
        try:
            with self._request_lock:
                page = self.get_page()
                if init_url:
                    page.goto(init_url, timeout=timeout, wait_until="domcontentloaded")
                    if wait_selector:
                        with suppress(PlaywrightTimeout):
                            page.wait_for_selector(wait_selector, timeout=3000)
                    page.wait_for_timeout(800)
                resp = page.context.request.get(url, timeout=timeout)
                if resp.status != HTTP_OK_STATUS:
                    logger.warning(f"Browser cookie 请求返回 {resp.status}: {url}")
                    return None
                try:
                    return resp.json()
                except Exception:
                    parsed = self._try_parse_json(resp.text())
                    if isinstance(parsed, dict):
                        return parsed
                    logger.warning(f"Browser cookie JSON 解析失败: {url}")
                    return None
        except Exception as e:
            logger.warning(f"Browser cookie 请求失败 {url}: {e}")
            self._reset_browser()
            return None
        finally:
            if page:
                with suppress(Exception):
                    page.close()

    def close(self):
        """关闭浏览器"""
        self._reset_browser()

    def __del__(self):
        self.close()


# 每线程实例容器（避免 Playwright 对象跨线程复用）
_browser_client_local = threading.local()


def get_browser_client() -> BrowserClient:
    """获取浏览器客户端（线程内单例）"""
    client = getattr(_browser_client_local, "client", None)
    if client is None:
        client = BrowserClient()
        _browser_client_local.client = client
    return client
