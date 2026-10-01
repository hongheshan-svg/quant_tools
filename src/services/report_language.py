"""AI 输出语言（报告语言）：zh 中文 / en 英文。

提示词输入仍用中文，en 模式下追加输出语言指令，让模型用英文写自由文本字段；
JSON 键和代码按值判断的枚举字段保持原样。代码生成的标题、标签、护栏说明用 tr() 给英文版。
"""
from __future__ import annotations

from typing import Any

LANGUAGES = ("zh", "en")

# 常用枚举的英文显示名
LABELS_EN: dict[str, str] = {
    "高": "High", "中": "Medium", "低": "Low",
    "看多": "Bullish", "中性": "Neutral", "看空": "Bearish",
    "进攻": "Offensive", "均衡": "Balanced", "防守": "Defensive",
    "买入": "Buy", "加仓": "Add", "持有": "Hold", "观望": "Watch",
    "减仓": "Reduce", "卖出": "Sell", "回避": "Avoid", "提醒": "Alert",
    "盘前": "Pre-market", "盘中": "Intraday", "午间休市": "Midday break",
    "临近收盘": "Near close", "盘后": "After-hours", "非交易日": "Non-trading day",
}


def report_language(config: dict | None) -> str:
    """读取 report.language，非法值按 zh。"""
    try:
        value = str(((config or {}).get("report") or {}).get("language") or "zh").strip().lower()
    except Exception:
        return "zh"
    return value if value in LANGUAGES else "zh"


def language_directive(lang: str, enums: str = "") -> str:
    """输出语言指令：zh 返回空串；en 返回英文指令。enums 描述必须保持原样的枚举字段。"""
    if lang != "en":
        return ""
    text = (
        "\n\nOutput language: write ALL free-text fields (summaries, reasons, analysis, "
        "risks, plans, conclusions) in English. Keep every JSON key exactly unchanged."
    )
    if enums:
        text += (
            " The following enumerated fields must keep their original values verbatim "
            f"(do NOT translate them, code relies on them): {enums}."
        )
    text += " Stock names, tickers and numbers stay as they are."
    return text


def tr(lang: str, zh: str, en: str) -> str:
    """按语言选文案。"""
    return en if lang == "en" else zh


def display(lang: str, value: Any) -> Any:
    """枚举值的显示名：en 时查 LABELS_EN，查不到原样返回。"""
    if lang != "en" or not isinstance(value, str):
        return value
    return LABELS_EN.get(value, value)
