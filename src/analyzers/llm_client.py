"""LLM 统一客户端，支持重试/超时/缓存/批量并发。

调用走 LiteLLM（参考 daily_stock_analysis），一套接口接入各家模型：
- anthropic（Claude）、gemini、ollama（本地）走 LiteLLM 的原生通道
- 其余平台（DeepSeek、通义千问、智谱、Kimi、文心、豆包、硅基流动、OpenAI、自定义）按 OpenAI 兼容协议调用 base_url
llm.backend 设为 openai 时改用 OpenAI SDK 直连（只支持 OpenAI 兼容平台）；LiteLLM 没装时也会自动退回。
每次调用（含缓存命中）都记录用量，见 src/analyzers/llm_usage.py。
"""

import base64
import hashlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from json_repair import repair_json
from loguru import logger

from src.analyzers.llm_usage import caller_feature, estimate_cost, import_litellm, record_usage

NATIVE_PROVIDERS = ("anthropic", "gemini", "ollama")   # 走 LiteLLM 原生通道的平台
KEYLESS_PROVIDERS = ("ollama",)                        # 不需要 API Key
NO_JSON_MODE = ("anthropic", "ollama")                 # 不传 response_format，靠提示词和 chat_json 的修复


@dataclass
class LLMRoute:
    """一个可调用的模型：provider/model 是配置里的名字，target 是 LiteLLM 的模型名。"""
    provider: str
    model: str
    target: str
    api_key: str
    api_base: str | None


def build_route(cfg: dict) -> "LLMRoute | None":
    """配置不可用（缺 Key 或还是示例占位符）时返回 None。"""
    provider = str(cfg.get("provider") or "openai").lower()
    model = str(cfg.get("model") or "").strip()
    api_key = str(cfg.get("api_key") or "")
    if not model:
        return None
    if provider not in KEYLESS_PROVIDERS and (not api_key or api_key.startswith("your-")):
        return None
    base_url = str(cfg.get("base_url") or "").strip() or None
    if provider in NATIVE_PROVIDERS:
        return LLMRoute(provider, model, f"{provider}/{model}", api_key, base_url)
    return LLMRoute(provider, model, f"openai/{model}", api_key, base_url or "https://api.deepseek.com")


class LLMClient:
    """LLM 统一调用客户端。"""

    def __init__(self, config: dict):
        self.cfg = config or {}
        self.primary_cfg = self.cfg.get("primary", {})
        self.backup_cfg = self.cfg.get("backup", {})
        self.vision_cfg = self.cfg.get("vision") or {}

        # 性能与鲁棒性配置（可在 settings.yaml.llm 下覆盖）
        self.timeout_seconds = int(self.cfg.get("timeout_seconds", 60))
        self.max_retries = int(self.cfg.get("max_retries", 1))
        self.retry_backoff_seconds = float(self.cfg.get("retry_backoff_seconds", 1.5))
        self.batch_workers = int(self.cfg.get("batch_workers", 3))

        # 本地缓存配置
        self.cache_enabled = bool(self.cfg.get("cache_enabled", True))
        self.cache_ttl_seconds = int(self.cfg.get("cache_ttl_seconds", 3600))
        cache_path = self.cfg.get("cache_path", "data/llm_cache.sqlite3")
        self.cache_path = Path(cache_path)
        self._cache_lock = threading.Lock()
        if self.cache_enabled:
            self._init_cache()

        self.backend = str(self.cfg.get("backend", "litellm"))
        self.pricing = self.cfg.get("pricing") or {}
        self.usage_path = self.cfg.get("usage_path", cache_path)
        self.primary_client = self._build_client(self.primary_cfg)
        self.backup_client = self._build_client(self.backup_cfg)
        self.vision_client = self._build_client(self.vision_cfg)
        self._openai_clients: dict[tuple, Any] = {}

    def reload(self, new_config: dict):
        """热重载：用新配置重建客户端，无需重启程序。"""
        self.cfg = new_config or {}
        self.primary_cfg = self.cfg.get("primary", {})
        self.backup_cfg = self.cfg.get("backup", {})
        self.vision_cfg = self.cfg.get("vision") or {}
        self.timeout_seconds = int(self.cfg.get("timeout_seconds", 60))
        self.max_retries = int(self.cfg.get("max_retries", 1))
        self.retry_backoff_seconds = float(self.cfg.get("retry_backoff_seconds", 1.5))
        self.batch_workers = int(self.cfg.get("batch_workers", 3))
        self.backend = str(self.cfg.get("backend", "litellm"))
        self.pricing = self.cfg.get("pricing") or {}
        self.primary_client = self._build_client(self.primary_cfg)
        self.backup_client = self._build_client(self.backup_cfg)
        self.vision_client = self._build_client(self.vision_cfg)
        provider = self.primary_cfg.get("provider", "unknown")
        model = self.primary_cfg.get("model", "unknown")
        logger.info(f"LLM 客户端已热重载: provider={provider}, model={model}")

    @staticmethod
    def _build_client(cfg: dict) -> LLMRoute | None:
        """配置可用时返回调用路由（缺 Key 或是示例占位符时为 None）。"""
        return build_route(cfg or {})

    def _cache_key(
        self,
        model: str,
        user_message: str,
        system_message: str,
        temperature: float | None,
        max_tokens: int | None,
        response_format: str | None,
    ) -> str:
        payload = {
            "model": model,
            "user_message": user_message,
            "system_message": system_message,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": response_format,
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _init_cache(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.cache_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_cache (
                    cache_key TEXT PRIMARY KEY,
                    response_text TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_cache_created ON llm_cache(created_at)")
            conn.commit()

    def _cache_get(self, key: str) -> str | None:
        if not self.cache_enabled:
            return None
        now = time.time()
        with self._cache_lock, sqlite3.connect(self.cache_path) as conn:
            row = conn.execute(
                "SELECT response_text, created_at FROM llm_cache WHERE cache_key=?",
                (key,),
            ).fetchone()
            if not row:
                return None
            resp_text, created_at = row
            if now - float(created_at) > self.cache_ttl_seconds:
                conn.execute("DELETE FROM llm_cache WHERE cache_key=?", (key,))
                conn.commit()
                return None
            return resp_text

    # 缓存最大条目数（超出后自动淘汰最旧的 20%）
    CACHE_MAX_ENTRIES = 2000
    CACHE_CLEANUP_CHECK_INTERVAL = 50
    _cache_cleanup_counter = 0

    def _cache_set(self, key: str, response_text: str):
        if not self.cache_enabled or not response_text:
            return
        with self._cache_lock, sqlite3.connect(self.cache_path) as conn:
            conn.execute(
                """
                    INSERT INTO llm_cache(cache_key, response_text, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        response_text=excluded.response_text,
                        created_at=excluded.created_at
                    """,
                (key, response_text, time.time()),
            )
            conn.commit()

            # 每写入 N 次检查一次缓存大小
            self._cache_cleanup_counter += 1
            if self._cache_cleanup_counter >= self.CACHE_CLEANUP_CHECK_INTERVAL:
                self._cache_cleanup_counter = 0
                self._cache_evict(conn)

    def _cache_evict(self, conn):
        """淘汰过期和超限的缓存条目。"""
        try:
            now = time.time()
            # 1. 删除过期条目
            conn.execute("DELETE FROM llm_cache WHERE ? - created_at > ?",
                         (now, self.cache_ttl_seconds))
            # 2. 如果仍超限，删除最旧的 20%
            count = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
            if count > self.CACHE_MAX_ENTRIES:
                to_delete = max(count - self.CACHE_MAX_ENTRIES, count // 5)
                conn.execute(
                    "DELETE FROM llm_cache WHERE cache_key IN ("
                    "  SELECT cache_key FROM llm_cache ORDER BY created_at ASC LIMIT ?"
                    ")", (to_delete,)
                )
            conn.commit()
            logger.debug(f"LLM 缓存清理完成，剩余 {count - to_delete if count > self.CACHE_MAX_ENTRIES else count} 条")
        except Exception as e:
            logger.debug(f"缓存清理异常: {e}")

    def chat(
        self,
        user_message: str,
        system_message: str = "",
        temperature: float = None,
        max_tokens: int = None,
        response_format: str = None,
    ) -> str:
        """
        统一聊天接口，先尝试主力模型，失败则回退备用模型

        Args:
            user_message: 用户消息
            system_message: 系统角色提示词
            temperature: 温度参数
            max_tokens: 最大生成token数
            response_format: 返回格式，"json" 时请求JSON模式

        Returns:
            LLM 返回的文本
        """
        # 缓存命中（只按主模型 key 缓存，命中直接返回）
        primary_model = self.primary_cfg.get("model", "deepseek-chat")
        cache_key = self._cache_key(
            model=primary_model,
            user_message=user_message,
            system_message=system_message,
            temperature=temperature if temperature is not None else self.primary_cfg.get("temperature", 0.3),
            max_tokens=max_tokens if max_tokens is not None else self.primary_cfg.get("max_tokens", 4096),
            response_format=response_format,
        )
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("LLM cache hit")
            record_usage(self.usage_path, provider=self.primary_cfg.get("provider", ""), model=primary_model,
                         feature=caller_feature(), cached=True)
            return cached

        # 尝试主力模型
        result = self._call(
            self.primary_client, self.primary_cfg,
            user_message, system_message, temperature, max_tokens, response_format,
        )
        if result is not None:
            self._cache_set(cache_key, result)
            return result

        # 回退备用模型
        if self.backup_client:
            logger.warning("主力模型调用失败，切换到备用模型")
            result = self._call(
                self.backup_client, self.backup_cfg,
                user_message, system_message, temperature, max_tokens, response_format,
            )
            if result is not None:
                self._cache_set(cache_key, result)
                return result

        raise RuntimeError("所有LLM模型均调用失败")

    def chat_vision(
        self,
        prompt: str,
        images: "list[tuple[bytes, str]]",
        system_message: str = "",
        max_tokens: int | None = None,
    ) -> str:
        """图片 + 文字提问（不走响应缓存）。

        路由顺序：llm.vision（已配置时）→ 主模型 → 备用模型，同一路由只试一次。
        消息用 OpenAI 多段格式，LiteLLM 会转换给 anthropic、gemini。
        """
        content: list[dict] = [{"type": "text", "text": prompt}]
        for data, mime in images:
            encoded = base64.b64encode(data).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})

        tried: set[tuple] = set()
        for name, route, cfg in (
            ("图片识别模型", self.vision_client, self.vision_cfg),
            ("主力模型", self.primary_client, self.primary_cfg),
            ("备用模型", self.backup_client, self.backup_cfg),
        ):
            if route is None:
                continue
            identity = (route.provider, route.model, route.api_base)
            if identity in tried:
                continue
            tried.add(identity)
            # 图片识别模型没单独配置 token 上限时沿用主力模型的
            result = self._call(route, {**self.primary_cfg, **cfg} if name == "图片识别模型" else cfg,
                                content, system_message, None, max_tokens, None)
            if result is not None:
                return result
            logger.warning(f"{name}图片识别失败，尝试下一个模型")
        raise RuntimeError("图片识别失败：没有可用的模型，或所有模型均调用失败（请确认模型支持图片输入）")

    def _call(
        self,
        client: LLMRoute | None,
        cfg: dict,
        user_message: "str | list",
        system_message: str,
        temperature: float,
        max_tokens: int,
        response_format: str,
    ) -> str | None:
        """调用单个模型"""
        if client is None:
            return None

        model = cfg.get("model", "deepseek-chat")
        temp = temperature if temperature is not None else cfg.get("temperature", 0.3)
        tokens = max_tokens if max_tokens is not None else cfg.get("max_tokens", 4096)

        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": user_message})

        kwargs = {
            "messages": messages,
            "temperature": temp,
            "max_tokens": tokens,
            "timeout": self.timeout_seconds,
        }
        if response_format == "json" and client.provider not in NO_JSON_MODE:
            kwargs["response_format"] = {"type": "json_object"}

        provider = client.provider
        feature = caller_feature()
        for attempt in range(1, self.max_retries + 2):
            started = time.monotonic()
            try:
                content, usage = self._complete(client, kwargs)
                prompt_tokens, completion_tokens = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
                record_usage(self.usage_path, provider=provider, model=model, feature=feature,
                             prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                             cost_usd=estimate_cost(provider, model, prompt_tokens, completion_tokens, self.pricing),
                             latency_ms=int((time.monotonic() - started) * 1000))
                logger.info(f"LLM [{provider}] 调用成功, model={model}, tokens={prompt_tokens + completion_tokens}")
                return content
            except Exception as e:
                err_str = str(e)[:120]
                if attempt >= self.max_retries + 1:
                    logger.error(f"LLM [{provider}] {self.max_retries+1}次均失败: {err_str}")
                    record_usage(self.usage_path, provider=provider, model=model, feature=feature, success=False,
                                 latency_ms=int((time.monotonic() - started) * 1000))
                    return None
                # 退避上限3秒，避免长时间等待
                delay = min(self.retry_backoff_seconds * (2 ** (attempt - 1)), 3.0)
                logger.warning(
                    f"LLM [{provider}] 第{attempt}次失败，{delay:.1f}s后重试: {err_str}"
                )
                time.sleep(delay)
        return None

    def _complete(self, route: LLMRoute, kwargs: dict) -> tuple[str, dict]:
        """发起一次请求，返回 (文本, {"prompt_tokens", "completion_tokens"})。"""
        if self.backend != "openai" and self._litellm_available():
            resp = import_litellm().completion(model=route.target, api_key=route.api_key or None, api_base=route.api_base, **kwargs)
        else:
            if route.provider in NATIVE_PROVIDERS:
                raise RuntimeError(f"{route.provider} 需要 LiteLLM（pip install litellm），当前 llm.backend=openai 或未安装")
            resp = self._openai_client(route).chat.completions.create(model=route.model, **kwargs)
        usage = getattr(resp, "usage", None)
        return resp.choices[0].message.content, {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
        }

    @staticmethod
    def _litellm_available() -> bool:
        try:
            import_litellm()
        except ImportError:
            return False
        return True

    def _openai_client(self, route: LLMRoute):
        key = (route.api_key, route.api_base)
        if key not in self._openai_clients:
            from openai import OpenAI

            self._openai_clients[key] = OpenAI(api_key=route.api_key, base_url=route.api_base)
        return self._openai_clients[key]

    def chat_json(
        self,
        user_message: str,
        system_message: str = "",
        temperature: float = None,
        max_tokens: int = None,
    ) -> dict:
        """
        请求 JSON 格式返回，并解析为字典

        Returns:
            解析后的字典，失败返回空字典
        """
        # JSON 模式需要更多 token，默认提高到 8192
        if max_tokens is None:
            max_tokens = 8192

        raw = self.chat(
            user_message=user_message,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format="json",
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # 尝试从 markdown code block 中提取 JSON
            try:
                if "```json" in raw:
                    json_str = raw.split("```json")[1].split("```")[0].strip()
                    return json.loads(json_str)
                if "```" in raw:
                    json_str = raw.split("```")[1].split("```")[0].strip()
                    return json.loads(json_str)
            except (json.JSONDecodeError, IndexError):
                pass

            # 尝试修复截断的 JSON（补全闭合括号）
            try:
                repaired = self._repair_truncated_json(raw)
                if repaired:
                    return repaired
            except Exception:
                pass

            # 最后兜底：json_repair 修复尾逗号、单引号、前后多余文字等格式问题。
            # 输出被截断时先截到最后一个完整对象，避免把半截的股票代码当成有效数据。
            try:
                text = (raw or "").strip()
                if not text.endswith("}") and "}" in text:
                    text = text[: text.rfind("}") + 1]
                repaired = repair_json(text, return_objects=True)
                if isinstance(repaired, dict) and repaired:
                    logger.warning(f"JSON 格式不规范，已自动修复: {raw[:80]}")
                    return repaired
            except Exception:
                pass

            logger.error(f"JSON 解析失败: {raw[:200]}")
            return {}

    def chat_json_batch(
        self,
        jobs: list[dict[str, Any]],
        max_workers: int | None = None,
    ) -> list[dict]:
        """
        并发执行多个 JSON 请求任务。
        jobs 元素格式:
            {
              "user_message": str,
              "system_message": str,
              "temperature": float | None,
              "max_tokens": int | None,
              "meta": Any
            }
        """
        if not jobs:
            return []
        worker_count = max_workers or self.batch_workers
        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="llm") as executor:
            future_map = {}
            for job in jobs:
                future = executor.submit(
                    self.chat_json,
                    user_message=job.get("user_message", ""),
                    system_message=job.get("system_message", ""),
                    temperature=job.get("temperature"),
                    max_tokens=job.get("max_tokens"),
                )
                future_map[future] = job.get("meta")

            for future in as_completed(future_map):
                meta = future_map[future]
                try:
                    payload = future.result() or {}
                    results.append({"meta": meta, "payload": payload})
                except Exception as e:
                    logger.error(f"LLM 批任务失败 [{meta}]: {e}")
                    results.append({"meta": meta, "payload": {}})
        return results

    @staticmethod
    def _repair_truncated_json(raw: str) -> dict | None:
        """尝试修复被截断的 JSON（DeepSeek 输出超过 max_tokens 时）"""
        text = raw.strip()
        if not text.startswith("{"):
            return None

        # 策略：找到最后一个完整的 item（以 } 结尾），补全 ] }
        # 找 "items": [ ... 的结构
        idx = text.rfind("}")
        if idx <= 0:
            return None

        # 尝试多种修复方式
        for suffix in ["]}", "]}}", "\n]}"]:
            candidate = text[:idx + 1] + suffix
            try:
                result = json.loads(candidate)
                if isinstance(result, dict) and "items" in result:
                    logger.warning(f"JSON 修复成功: 截断后恢复 {len(result.get('items', []))} 条")
                    return result
            except json.JSONDecodeError:
                continue

        return None
