"""
自定义报告模板（Jinja2）：个股诊断、自选股决策仪表盘、大盘复盘、日报。

用户模板放在数据目录的 config/templates/{name}.md.j2，没有模板时报告逐字沿用内置格式；
模板出错（语法、运行时、结果为空）时回退内置格式并记 warning。模板在沙箱环境里渲染，按文件修改时间缓存。
内置示例模板在 src/services/templates/*.md.j2（打包时作为数据文件内置）。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, TemplateSyntaxError
from jinja2.sandbox import ImmutableSandboxedEnvironment
from loguru import logger

TEMPLATE_NAMES = {"diagnosis": "个股诊断", "watchlist": "自选股决策仪表盘", "market_review": "大盘复盘", "daily_report": "日报"}
BUILTIN_DIR = Path(__file__).parent / "templates"
CUSTOM_DIR = Path("config/templates")  # 与 settings.yaml 同在数据目录下（相对工作目录，同 settings_store.SETTINGS_PATH）
SUFFIX = ".md.j2"

_cache: dict[tuple[str, int, int], Any] = {}
_lock = threading.Lock()


def templates_dir() -> Path:
    """用户模板目录（config/templates）。"""
    return CUSTOM_DIR


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def _env() -> ImmutableSandboxedEnvironment:
    # 不可变沙箱：模板不能访问内部属性，也不能修改传入的数据（如 risks.append）
    return ImmutableSandboxedEnvironment(autoescape=False, undefined=ChainableUndefined, trim_blocks=True, lstrip_blocks=True)


def _check_name(name: str) -> None:
    if name not in TEMPLATE_NAMES:
        raise KeyError(name)


def _custom_path(name: str) -> Path:
    return templates_dir() / f"{name}{SUFFIX}"


def _compiled(path: Path):
    """按文件路径和修改时间缓存编译后的模板；文件变化后旧缓存条目被丢弃。"""
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _lock:
        template = _cache.get(key)
        if template is None:
            for old in [k for k in _cache if k[0] == key[0]]:
                del _cache[old]
            template = _env().from_string(path.read_text(encoding="utf-8"))
            _cache[key] = template
        return template


def render_report(name: str, data: dict, fallback: str) -> str:
    """有自定义模板时用模板渲染 data（另提供变量 default=fallback），出错或结果为空白时回退 fallback。"""
    if name not in TEMPLATE_NAMES:
        return fallback
    try:
        path = _custom_path(name)
        if not path.is_file():
            return fallback
        text = _compiled(path).render(**{**data, "default": fallback})
    except Exception as e:  # noqa: BLE001 模板的任何错误都不能影响报告生成
        logger.warning(f"报告模板 {name} 渲染失败，改用内置格式: {e}")
        return fallback
    if not text.strip():
        logger.warning(f"报告模板 {name} 渲染结果为空，改用内置格式")
        return fallback
    return text


def _builtin_text(name: str) -> str:
    path = BUILTIN_DIR / f"{name}{SUFFIX}"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def list_templates() -> list[dict[str, Any]]:
    return [{"name": name, "label": label, "custom": _custom_path(name).is_file(), "path": str(_custom_path(name))}
            for name, label in TEMPLATE_NAMES.items()]


def get_template(name: str) -> str:
    """自定义模板文本；没有自定义模板时返回内置示例模板。"""
    _check_name(name)
    path = _custom_path(name)
    return path.read_text(encoding="utf-8") if path.is_file() else _builtin_text(name)


def save_template(name: str, text: str) -> None:
    """保存自定义模板；语法错误抛 ValueError（带行号）。"""
    _check_name(name)
    try:
        _env().parse(text)
    except TemplateSyntaxError as e:
        raise ValueError(f"第 {e.lineno} 行语法错误：{e.message}") from e
    path = _custom_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    reset_cache()


def delete_template(name: str) -> bool:
    """删除自定义模板（恢复内置格式）；返回是否删除了文件。"""
    _check_name(name)
    path = _custom_path(name)
    existed = path.is_file()
    if existed:
        path.unlink()
    reset_cache()
    return existed


# ---------- 预览 ----------

SAMPLE_DATA: dict[str, dict[str, Any]] = {
    "diagnosis": {
        "name": "示例股份", "code": "600000", "action": "buy", "action_label": "买入", "score": 72, "confidence": "中",
        "one_sentence": "趋势向上，量能配合，可逢低布局。", "created_at": "2026-01-02 15:40", "trade_date": "2026-01-02",
        "guardrails": [], "position_advice": {"no_position": "回踩均线分批建仓", "has_position": "持股待涨"},
        "battle_plan": {"buy_price": 10.5, "stop_loss": 9.8, "target_price": 12.0, "suggested_position": "三成仓"},
        "theme_role": {"theme": "示例题材", "phase": "加速", "role": "龙头"}, "market_regime": "均衡",
        "catalysts": ["行业景气回升"], "risks": ["大盘波动风险"],
        "checklist": [{"status": "pass", "item": "趋势", "note": "多头排列"}], "analysis": "示例综合分析。",
        "language": "zh",
    },
    "watchlist": {
        "trade_date": "2026-01-02",
        "items": [{"code": "600000", "name": "示例股份", "action": "buy", "action_label": "买入", "score": 72,
                   "one_sentence": "趋势向上。", "battle_plan": {"buy_price": 10.5, "stop_loss": 9.8, "target_price": 12.0},
                   "catalysts": ["行业景气回升"], "risks": ["大盘波动风险"], "guardrails": [], "change": "首次诊断"}],
        "failed": [], "counts": {"买入/加仓": 1, "持有/观望": 0, "减仓/卖出/回避": 0}, "language": "zh",
    },
    "market_review": {
        "trade_date": "2026-01-02", "headline": "缩量震荡，题材轮动加快", "trend": "指数围绕均线震荡。",
        "emotion": "涨停家数回落，情绪降温。", "main_lines": "示例题材延续强势。", "stance": "均衡", "position": "四到五成",
        "focus": ["示例题材龙头"], "avoid": ["高位放量股"], "watch_points": ["成交额能否放大"], "guardrails": [],
        "regime": "大盘环境：均衡", "language": "zh",
    },
    "daily_report": {
        "title": "A股量化日报 2026-01-02", "date": "2026-01-02",
        "market": "### 大盘复盘\n- 上涨 2000 / 下跌 2500", "review": "", "themes": "### 主线梯队\n- 题材｜示例题材",
        "signals": "### 今日信号\n- 600000 示例股份", "orders": "", "account": "", "performance": "", "source_health": "",
        "disclaimer": "> 仅供学习研究，不构成投资建议", "language": "zh",
    },
}
SAMPLE_DATA["daily_report"]["sections"] = [v for k, v in SAMPLE_DATA["daily_report"].items()
                                           if k in ("market", "review", "themes", "signals", "orders", "account", "performance",
                                                    "source_health", "disclaimer") and v]


def _db_path(config: dict | None) -> str:
    if config is None:
        from src.config_loader import load_config

        config = load_config()
    return (config.get("database") or {}).get("sqlite_path", "data/quant.db")


def _latest_data(name: str, config: dict | None) -> tuple[dict[str, Any], str] | None:
    """最近一份真实数据及其内置格式；没有数据或出错返回 None。"""
    try:
        db_path = _db_path(config)
        if name == "diagnosis":
            from src.services.data_query_service import DataQueryService
            from src.services.stock_diagnosis import render_markdown

            service = DataQueryService(db_path)
            rows = service.list_diagnoses(days=0, limit=1)["items"]
            row = service.get_diagnosis(rows[0]["id"]) if rows else None
            return (row["result"], render_markdown(row["result"])) if row else None
        if name == "watchlist":
            from src.services.watchlist_report import BUCKETS, WatchlistReportService, bucket_of, render_dashboard

            report = WatchlistReportService(config or {"database": {"sqlite_path": db_path}}).latest()
            if not report:
                return None
            counts = {label: 0 for _, label, _ in BUCKETS}
            for it in report["items"]:
                counts[bucket_of(it["action"])[1]] += 1
            data = {"trade_date": report["trade_date"], "items": report["items"], "failed": report["failed"], "counts": counts}
            return data, render_dashboard(report["trade_date"], report["items"], report["failed"])
        if name == "market_review":
            from src.services.market_review import MarketReviewService, render_markdown

            review = MarketReviewService(config or {"database": {"sqlite_path": db_path}}).get()
            return (review, render_markdown(review)) if review and not review.get("error") else None
    except Exception as e:  # noqa: BLE001 没有数据或数据不完整时改用示例数据
        logger.info(f"报告模板预览取最近数据失败，改用示例数据: {e}")
    return None


def _sample_fallback(name: str, data: dict[str, Any]) -> str:
    """示例数据对应的内置格式（日报没有独立的渲染函数，用各段拼接）。"""
    try:
        if name == "diagnosis":
            from src.services.stock_diagnosis import render_markdown
            return render_markdown(data)
        if name == "watchlist":
            from src.services.watchlist_report import render_dashboard
            return render_dashboard(data["trade_date"], data["items"], data["failed"])
        if name == "market_review":
            from src.services.market_review import render_markdown
            return render_markdown(data)
    except Exception as e:  # noqa: BLE001
        logger.info(f"报告模板预览生成内置格式失败: {e}")
    return "\n\n".join(s for s in (data.get(k) for k in ("market", "review", "themes", "signals", "orders", "account",
                                                         "performance", "source_health", "disclaimer")) if s)


def preview(name: str, text: str, data: dict | None = None, config: dict | None = None) -> str:
    """用最近一份真实数据（没有时用示例数据）渲染 text；语法或运行错误抛 ValueError。"""
    _check_name(name)
    if data is None:
        found = _latest_data(name, config)
        if found:
            data, fallback = found
        else:
            data = SAMPLE_DATA[name]
            fallback = _sample_fallback(name, data)
    else:
        fallback = _sample_fallback(name, data)
    try:
        return _env().from_string(text).render(**{**data, "default": fallback})
    except TemplateSyntaxError as e:
        raise ValueError(f"第 {e.lineno} 行语法错误：{e.message}") from e
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"渲染失败：{e}") from e
