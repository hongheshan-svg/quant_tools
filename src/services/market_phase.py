"""
市场阶段感知（参考 daily_stock_analysis 的 phase_decision）

按 A 股交易时段（盘前、盘中、午间休市、临近收盘、盘后、非交易日）给诊断不同口径的约束：
- 盘前/非交易日不给「立即买卖」，只给开盘计划
- 盘中标记当天 K 线未完成，涨跌幅和成交额是盘中数据
- 行情日期落后于最近完整交易日时提示数据过时并降级

纯逻辑，不联网；交易日判断只查 trading_calendar 的内存日历。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from src import trading_calendar
from src.analyzers.decision import BULLISH_ACTIONS
from src.services.report_language import display, tr

PHASE_LABELS = {
    "premarket": "盘前", "intraday": "盘中", "lunch_break": "午间休市",
    "closing_auction": "临近收盘", "postmarket": "盘后", "non_trading": "非交易日",
}
PARTIAL_BAR_PHASES = ("intraday", "lunch_break", "closing_auction")
OPEN_MIN = 9 * 60 + 30
NOON_START = 11 * 60 + 30
NOON_END = 13 * 60
AUCTION_START = 14 * 60 + 57
CLOSE_MIN = 15 * 60
MAX_LOOKBACK_DAYS = 30
MAX_TEXT_LEN = 120
MAX_LIST_ITEMS = 5
IMMEDIATE_WORDS = ("立即买入", "马上买入", "立即加仓", "马上加仓", "立即卖出", "马上卖出", "立即减仓", "马上减仓")
NEGATIONS = ("暂不", "不建议", "不要", "无需", "避免", "不能", "不宜", "勿", "不")
NEGATION_WINDOW = 6
PREMARKET_REPLACEMENT = "等待开盘后确认承接，不追价"
NON_TRADING_REPLACEMENT = "非交易日，下一交易日开盘后再按计划执行"
PREMARKET_REPLACEMENT_EN = "Wait for the open to confirm buying interest; do not chase the price"
NON_TRADING_REPLACEMENT_EN = "Non-trading day; execute the plan after the next trading day opens"
IMMEDIATE_EN = re.compile(r"\b(?:buy|sell|add|reduce)\s+(?:now|immediately|right away)\b|\b(?:immediately|right away)\s+(?:buy|sell|add|reduce)\b", re.I)
NEGATION_EN = re.compile(r"\b(?:do not|don't|not|never|avoid|no need to|wait)\b", re.I)


def prev_trade_day(d: date | datetime) -> date:
    """d 之前（不含 d）最近的交易日，最多向前找 30 天，找不到时返回 d 前一天。"""
    day = d.date() if isinstance(d, datetime) else d
    for i in range(1, MAX_LOOKBACK_DAYS + 1):
        cand = day - timedelta(days=i)
        if trading_calendar.is_trade_day(cand):
            return cand
    return day - timedelta(days=1)


def _minutes_to_close(minute: int) -> int:
    """距 15:00 剩余的交易分钟数（扣除 11:30-13:00 午休）。"""
    if minute < NOON_START:
        return (NOON_START - minute) + (CLOSE_MIN - NOON_END)
    if minute < NOON_END:
        return CLOSE_MIN - NOON_END
    return max(0, CLOSE_MIN - minute)


def current_phase(now: datetime | None = None) -> dict[str, Any]:
    """当前所处的 A 股时段，区间左闭右开。"""
    now = now or datetime.now()
    today = now.date()
    minute = now.hour * 60 + now.minute
    is_td = trading_calendar.is_trade_day(now)
    if not is_td:
        phase = "non_trading"
    elif minute < OPEN_MIN:
        phase = "premarket"
    elif minute < NOON_START:
        phase = "intraday"
    elif minute < NOON_END:
        phase = "lunch_break"
    elif minute < AUCTION_START:
        phase = "intraday"
    elif minute < CLOSE_MIN:
        phase = "closing_auction"
    else:
        phase = "postmarket"
    bar_date = today if phase == "postmarket" else prev_trade_day(today)
    return {
        "phase": phase,
        "label": PHASE_LABELS[phase],
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "is_trading_day": is_td,
        "is_partial_bar": phase in PARTIAL_BAR_PHASES,
        "minutes_to_open": OPEN_MIN - minute if phase == "premarket" else None,
        "minutes_to_close": _minutes_to_close(minute) if phase in PARTIAL_BAR_PHASES else None,
        "effective_daily_bar_date": bar_date.strftime("%Y-%m-%d"),
    }


def _is_stale(ctx: dict, quote_trade_date: str) -> bool:
    eff = ctx.get("effective_daily_bar_date")
    return bool(eff and quote_trade_date and str(quote_trade_date) < str(eff))


def _is_today(ctx: dict, quote_trade_date: str) -> bool:
    return bool(quote_trade_date) and str(quote_trade_date) == str(ctx.get("now", ""))[:10]


def phase_prompt_section(ctx: dict, quote_trade_date: str) -> str:
    """提示词里的一行【市场阶段】。"""
    phase = ctx.get("phase", "")
    label = ctx.get("label") or PHASE_LABELS.get(phase, "")
    eff = ctx.get("effective_daily_bar_date") or "未知"
    parts = [f"当前{label}，时间 {ctx.get('now', '')}，最近完整日线 {eff}"]
    if phase == "premarket":
        parts.append(f"尚未开盘，不得描述今日走势已经发生；只能基于上一完整交易日（{eff}）和盘前信息给开盘计划、观察价位和风险预案")
    elif phase in PARTIAL_BAR_PHASES:
        parts.append("不是盘后复盘，聚焦当前盘中状态、观察条件和下一次检查时间")
        if ctx.get("is_partial_bar") and _is_today(ctx, quote_trade_date):
            parts.append("今天的日线尚未走完，涨跌幅和成交额是盘中数据，不能当完整日线复盘")
        if phase == "lunch_break":
            parts.append("下午开盘后再确认")
        elif phase == "closing_auction":
            parts.append("侧重收盘前风控和是否隔夜持仓")
    elif phase == "postmarket":
        parts.append("可以按完整交易日复盘")
    elif phase == "non_trading":
        parts.append(f"今天不是交易日，只能基于上一完整交易日（{eff}）和已知事件分析，不得编造今日走势")
    if _is_stale(ctx, quote_trade_date):
        parts.append(f"行情停留在 {quote_trade_date}，早于最近完整交易日 {eff}，数据可能过时")
    return "【市场阶段】" + "；".join(parts)


def _text(value: Any) -> str:
    return str(value).strip()[:MAX_TEXT_LEN] if value is not None else ""


def _text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return []
    return [_text(v) for v in value if v is not None and str(v).strip()][:MAX_LIST_ITEMS]


def _has_immediate_trade(text: str) -> bool:
    """含未被否定的「立即买入」等表述。"""
    for word in IMMEDIATE_WORDS:
        for m in re.finditer(re.escape(word), text):
            before = text[max(0, m.start() - NEGATION_WINDOW):m.start()]
            if not any(n in before for n in NEGATIONS):
                return True
    return False


def _has_immediate_trade_en(text: str) -> bool:
    """含未被否定的英文「立即买卖」表述（如 buy now）。"""
    for m in IMMEDIATE_EN.finditer(text):
        if not NEGATION_EN.search(text[max(0, m.start() - 24):m.start()]):
            return True
    return False


def phase_guardrails(action: str, confidence: str, phase_decision: Any, ctx: dict,
                     quote_trade_date: str, lang: str = "zh", *, fetched_at=None) -> tuple[str, str, dict, list[str]]:
    """阶段护栏：返回（action, confidence, 规范化的 phase_decision, 护栏说明）。"""
    raw = phase_decision if isinstance(phase_decision, dict) else {}
    phase = ctx.get("phase", "")
    decision = {
        "phase": phase,
        "phase_label": ctx.get("label") or PHASE_LABELS.get(phase, ""),
        "trading_window": _text(raw.get("trading_window")),
        "immediate_action": _text(raw.get("immediate_action")),
        "watch_conditions": _text_list(raw.get("watch_conditions")),
        "next_check_time": _text(raw.get("next_check_time")),
        "data_limitations": _text_list(raw.get("data_limitations")),
    }
    notes: list[str] = []
    immediate = _has_immediate_trade(decision["immediate_action"]) or (
        lang == "en" and _has_immediate_trade_en(decision["immediate_action"]))
    if phase in ("premarket", "non_trading") and immediate:
        decision["immediate_action"] = tr(
            lang, PREMARKET_REPLACEMENT if phase == "premarket" else NON_TRADING_REPLACEMENT,
            PREMARKET_REPLACEMENT_EN if phase == "premarket" else NON_TRADING_REPLACEMENT_EN)
        if confidence == "高":
            confidence = "中"
        notes.append(tr(lang, f"当前为{decision['phase_label']}，不支持立即买卖，已改为开盘后确认",
                        f"Current phase is {display(lang, decision['phase_label'])}; immediate buy/sell is not supported, changed to confirm after the open"))
    limits = decision["data_limitations"]
    if phase in PARTIAL_BAR_PHASES and _is_today(ctx, quote_trade_date):
        limits.append(tr(lang, "今日 K 线尚未走完，涨跌幅、成交额为盘中数据",
                         "Today's daily bar is incomplete; change % and turnover are intraday figures"))
    from src.services.data_freshness import daily_quality
    freshness = daily_quality(quote_trade_date, fetched_at, phase=ctx, allow_partial=True)
    if freshness['status'] in {'stale', 'missing'}:
        eff = ctx.get("effective_daily_bar_date") or '未知'
        limits.extend(freshness['limitations'])
        limits.append(tr(lang, f"行情停留在 {quote_trade_date}（最近完整交易日 {eff}）",
                         f"Quotes are stuck at {quote_trade_date} (latest complete trading day {eff})"))
        if confidence == "高":
            confidence = "中"
        if action in BULLISH_ACTIONS:
            action = "watch"
            notes.append(tr(lang, f"行情数据停留在 {quote_trade_date}，未满足最近完整交易日 {eff} 的时效要求，无法确认买点，降级为观望",
                            f"Quote data is stuck at {quote_trade_date}, not valid for the latest complete trading day {eff}; "
                            "entry cannot be confirmed, downgraded to Watch"))
    return action, confidence, decision, notes
