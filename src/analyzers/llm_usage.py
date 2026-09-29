"""
大模型调用用量记录（参考 daily_stock_analysis 的 Token 用量页）

每次调用（包括缓存命中）记一行到 LLM 缓存库（data/llm_cache.sqlite3）的 llm_usage 表：
平台、模型、功能、输入/输出 token、估算费用（美元）、耗时、是否命中缓存、是否成功。
- 功能按调用方所在模块归类（个股诊断、AI 问股、大盘复盘……），不需要各处传参数，跨线程也能识别
- 费用：优先用 llm.pricing 里配置的单价（每百万 token 美元，[输入, 输出]），否则查 LiteLLM 自带的价格表，查不到记为空
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

FEATURE_LABELS = {
    "src.services.stock_diagnosis": "个股诊断",
    "src.services.diagnosis_agents": "个股诊断",
    "src.services.stock_chat": "AI 问股",
    "src.services.market_review": "大盘复盘",
    "src.services.premarket_predictor": "涨停预测",
    "src.services.trade_advisor": "AI 研判",
    "src.analyzers.sentiment": "舆情分析",
    "src.analyzers.topic_extractor": "题材提取",
    "src.analyzers.global_impact": "国际因子",
    "src.analyzers.limit_up": "涨停分析",
    "src.services.image_import": "图片识别",
    "api.v1.system": "连接测试",
}
SKIP_MODULES = ("src.analyzers.llm_client", "src.analyzers.llm_usage")

_lock = threading.Lock()
_initialized: set[str] = set()


def import_litellm():
    """导入 LiteLLM：用自带的价格表（默认导入时会去 GitHub 下载），关闭调试输出。"""
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    litellm.suppress_debug_info = True
    return litellm


def caller_feature() -> str:
    """沿调用栈找到第一个项目内（src./api.）且不是 LLM 客户端本身的模块，归到对应功能。"""
    frame = sys._getframe(1)
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if module.startswith(("src.", "api.")) and not module.startswith(SKIP_MODULES):
            return FEATURE_LABELS.get(module, module.rsplit(".", 1)[-1])
        frame = frame.f_back
    return "其他"


def estimate_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int, pricing: dict | None = None) -> float | None:
    """估算费用（美元）。"""
    price = (pricing or {}).get(model)
    if isinstance(price, (list, tuple)) and len(price) == 2:
        return round((prompt_tokens * float(price[0]) + completion_tokens * float(price[1])) / 1e6, 6)
    try:
        litellm = import_litellm()
        for key in (f"{provider}/{model}", model):
            info = litellm.model_cost.get(key)
            if info and info.get("input_cost_per_token") is not None:
                return round(prompt_tokens * info["input_cost_per_token"] + completion_tokens * (info.get("output_cost_per_token") or 0), 6)
    except Exception:
        return None
    return None


def _ensure(conn: sqlite3.Connection, path: str) -> None:
    if path in _initialized:
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            provider TEXT, model TEXT, feature TEXT,
            prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0, total_tokens INTEGER DEFAULT 0,
            cost_usd REAL, latency_ms INTEGER, cached INTEGER DEFAULT 0, success INTEGER DEFAULT 1
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at)")
    _initialized.add(path)


def record_usage(path: str | Path, *, provider: str, model: str, feature: str, prompt_tokens: int = 0, completion_tokens: int = 0,
                 cost_usd: float | None = None, latency_ms: int | None = None, cached: bool = False, success: bool = True) -> None:
    path = str(path)
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with _lock, sqlite3.connect(path) as conn:
            _ensure(conn, path)
            conn.execute(
                "INSERT INTO llm_usage(created_at, provider, model, feature, prompt_tokens, completion_tokens, total_tokens, "
                "cost_usd, latency_ms, cached, success) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), provider, model, feature, prompt_tokens, completion_tokens, prompt_tokens + completion_tokens,
                 cost_usd, latency_ms, int(cached), int(success)),
            )
    except Exception:  # 用量记录失败不能影响业务
        pass


def usage_summary(path: str | Path, days: int = 30, now: float | None = None) -> dict[str, Any]:
    """近 days 天的用量：总计、按天、按功能、按模型。"""
    path = str(path)
    empty = {"days": days, "total": {"calls": 0, "cached": 0, "failed": 0, "tokens": 0, "cost_usd": 0.0},
             "by_day": [], "by_feature": [], "by_model": []}
    if not Path(path).exists():
        return empty
    since = (now or time.time()) - days * 86400
    with _lock, sqlite3.connect(path) as conn:
        _ensure(conn, path)
        base = "FROM llm_usage WHERE created_at >= ?"
        agg = ("COUNT(*), SUM(cached), SUM(1 - success), COALESCE(SUM(total_tokens), 0), COALESCE(SUM(cost_usd), 0), "
               "COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0)")

        def rows(group: str, order: str) -> list[dict[str, Any]]:
            result = []
            for r in conn.execute(f"SELECT {group}, {agg} {base} GROUP BY 1 ORDER BY {order}", (since,)):
                result.append({"key": r[0], "calls": r[1], "cached": r[2] or 0, "failed": r[3] or 0, "tokens": r[4],
                               "cost_usd": round(r[5], 4), "prompt_tokens": r[6], "completion_tokens": r[7]})
            return result

        total = conn.execute(f"SELECT {agg} {base}", (since,)).fetchone()
        return {
            "days": days,
            "total": {"calls": total[0], "cached": total[1] or 0, "failed": total[2] or 0, "tokens": total[3],
                      "cost_usd": round(total[4], 4), "prompt_tokens": total[5], "completion_tokens": total[6]},
            "by_day": rows("date(created_at, 'unixepoch', 'localtime')", "1"),
            "by_feature": rows("feature", "5 DESC"),
            "by_model": rows("provider || '/' || model", "5 DESC"),
        }
