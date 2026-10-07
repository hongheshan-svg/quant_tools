"""
决策风格（保守 / 均衡 / 进取）与诊断护栏的纯函数 decide()

同一份分析可以按不同风格重新评估：护栏阈值随风格变化，只使用诊断时保存的快照（inputs），
不重新调用模型、不联网、不读取当前行情。balanced 的阈值与诊断护栏的原有常量完全一致。

所有风格都保留的安全护栏（不随风格放松）：大盘冰点不开仓、核心行情/日线不足、近 30 天严重风险公告、
行情过时（阶段护栏）、与上次诊断方向反复。

inputs 快照字段（均可 JSON 序列化）：
- score：原始评分（0~100）
- action：原始操作建议（归一化后，缺失时已按评分推断）
- confidence：原始信心（高/中/低）
- regime：{"regime": 大盘档位, "position_factor": 仓位系数}
- data_quality：{"score", "missing", "core_ok", "bar_count"}
- flow_ratio：资金净流出占成交额的比例（%，负数为净流出），没有为 None
- severe_notice：近 30 天严重风险公告 {"date", "title", "risk"}，没有为 None
- disagreement：分析员分歧描述，没有为空串
- calibration_bullish：看多诊断历史校准 {"n", "accuracy"}，没有为 None
- previous：3 天内上次诊断 {"action", "score", "created_at", "action_label"}，没有为 None
- phase：阶段上下文子集 {"phase", "label", "now", "effective_daily_bar_date"}，没有为 None
- quote_trade_date：行情日期
- phase_decision：LLM 给的阶段决策原文（dict）
- invalidation：LLM 给的失效条件；stop_loss：LLM 给的止损价
"""

from __future__ import annotations

from typing import Any

from src.analyzers.decision import ACTION_LABELS, BULLISH_ACTIONS
from src.services import market_phase
from src.services.report_language import display, tr

PROFILES = ("conservative", "balanced", "aggressive")
PROFILE_LABELS = {"conservative": "保守", "balanced": "均衡", "aggressive": "进取"}
PROFILE_LABELS_EN = {"conservative": "Conservative", "balanced": "Balanced", "aggressive": "Aggressive"}
DEFAULT_PROFILE = "balanced"

BEARISH_ACTIONS = frozenset({"reduce", "sell", "avoid"})
STABILITY_DAYS = 3
STABILITY_SCORE_DELTA = 15
MIN_CALIBRATION_SAMPLES = 10       # 历史校准：看多诊断至少验证过 10 次才生效
LOW_CALIBRATION_ACCURACY = 45.0    # 看多诊断 3 日准确率低于该值时下调买入信心
CONSERVATIVE_POSITION_NOTE = "保守风格：单只不超过 2 成"
CONSERVATIVE_POSITION_NOTE_EN = "Conservative style: no more than 20% of the portfolio in a single stock"

# 各风格阈值；balanced 必须与诊断护栏原有常量一致（评分 50、数据完整度 60%、资金净流出 -5%）
PROFILE_RULES: dict[str, dict[str, Any]] = {
    "conservative": {
        "min_score": 65, "min_data_quality": 75, "flow_outflow_ratio": -3.0,
        "defensive_blocks": True, "low_confidence_blocks": True, "require_invalidation": True, "position_note": True,
    },
    "balanced": {
        "min_score": 50, "min_data_quality": 60, "flow_outflow_ratio": -5.0,
        "defensive_blocks": False, "low_confidence_blocks": False, "require_invalidation": False, "position_note": False,
    },
    "aggressive": {
        "min_score": 45, "min_data_quality": 50, "flow_outflow_ratio": -8.0,
        "defensive_blocks": False, "low_confidence_blocks": False, "require_invalidation": True, "position_note": False,
    },
}
PROFILE_DESCRIPTIONS = {
    "conservative": "评分 65 以上才买入，数据完整度 75% 以上，大盘防守不开仓，信心低不买，买入必须有失效条件或止损价",
    "balanced": "评分 50 以上、数据完整度 60% 以上才买入（默认）",
    "aggressive": "评分 45 以上、数据完整度 50% 以上可买入，资金净流出容忍到 8%，买入必须有失效条件或止损价",
}


def normalize_profile(value: Any) -> str:
    """规范化决策风格；非法或空值按 balanced。"""
    text = str(value or "").strip().lower()
    return text if text in PROFILES else DEFAULT_PROFILE


def profile_label(profile: str, lang: str = "zh") -> str:
    profile = normalize_profile(profile)
    return PROFILE_LABELS_EN[profile] if lang == "en" else PROFILE_LABELS[profile]


def decide(inputs: dict, profile: str, lang: str = "zh") -> tuple[str, str, list[str]]:
    """按风格对快照做护栏判定，返回（action, confidence, 护栏说明）。纯函数，不联网、不调用模型。"""
    action, confidence, notes, _ = decide_with_phase(inputs, profile, lang)
    return action, confidence, notes


def decide_with_phase(inputs: dict, profile: str, lang: str = "zh") -> tuple[str, str, list[str], dict]:
    """decide() 的完整版：额外返回规范化后的 phase_decision（没有阶段上下文时为空 dict）。"""
    profile = normalize_profile(profile)
    rules = PROFILE_RULES[profile]
    plabel = profile_label(profile, lang)
    styled = profile != DEFAULT_PROFILE
    score = float(inputs.get("score", 50))
    action = str(inputs.get("action") or "watch")
    confidence = str(inputs.get("confidence") or "")
    guardrails: list[str] = []

    def downgrade(zh: str, en: str, zh_styled: str = "", en_styled: str = "") -> None:
        if styled and zh_styled:
            guardrails.append(tr(lang, f"{PROFILE_LABELS[profile]}风格：{zh_styled}", f"{plabel} style: {en_styled}"))
        else:
            guardrails.append(tr(lang, zh, en))

    min_score = rules["min_score"]
    if action in BULLISH_ACTIONS and score < min_score:
        downgrade(f"评分 {score:.0f} 与「{ACTION_LABELS[action]}」不一致，降级为观望",
                  f"Score {score:.0f} is inconsistent with \"{display(lang, ACTION_LABELS[action])}\", downgraded to Watch",
                  f"评分 {score:.0f} 低于 {min_score}，降级为观望",
                  f"score {score:.0f} is below {min_score}, downgraded to Watch")
        action = "watch"
    regime = inputs.get("regime") or {}
    regime_name = regime.get("regime")
    factor = float(regime.get("position_factor") or 0)
    if action in BULLISH_ACTIONS and regime_name == "冰点":
        guardrails.append(tr(lang, "大盘处于冰点，暂停开新仓，降级为观望",
                             "Market is at freezing point; new positions suspended, downgraded to Watch"))
        action = "watch"
    elif action in BULLISH_ACTIONS and regime_name == "防守":
        if rules["defensive_blocks"]:
            downgrade("", "", "大盘防守，不开新仓，降级为观望", "market is defensive; no new positions, downgraded to Watch")
            action = "watch"
        else:
            guardrails.append(tr(lang, f"大盘防守，新开仓仓位按 ×{factor:.1f} 控制",
                                 f"Market is defensive; new position size scaled by x{factor:.1f}"))

    quality = inputs.get("data_quality") or {}
    q_score, missing, bar_count = quality.get("score", 0), quality.get("missing") or [], quality.get("bar_count", 0)
    if action in BULLISH_ACTIONS and not quality.get("core_ok"):
        guardrails.append(tr(lang, f"行情或日线数据不足（日线 {bar_count} 根），无法确认买点，降级为观望",
                             f"Quote or daily data insufficient ({bar_count} daily bars); entry cannot be confirmed, downgraded to Watch"))
        action = "watch"
    min_quality = rules["min_data_quality"]
    if q_score < min_quality:
        confidence = "低"
        if action in BULLISH_ACTIONS:
            downgrade(f"数据完整度 {q_score}%（缺少{'、'.join(missing)}），不足以支撑买入，降级为观望",
                      f"Data completeness {q_score}% (missing: {', '.join(missing)}) is not enough to support a buy, downgraded to Watch",
                      f"数据完整度 {q_score}%（缺少{'、'.join(missing)}）低于 {min_quality}%，降级为观望",
                      f"data completeness {q_score}% (missing: {', '.join(missing)}) is below {min_quality}%, downgraded to Watch")
            action = "watch"
    flow_ratio = inputs.get("flow_ratio")
    if action in BULLISH_ACTIONS and flow_ratio is not None and flow_ratio <= rules["flow_outflow_ratio"]:
        downgrade(f"资金净流出占成交额 {abs(flow_ratio):.1f}%，与买入建议矛盾，降级为观望",
                  f"Net fund outflow is {abs(flow_ratio):.1f}% of turnover, contradicting the buy advice, downgraded to Watch",
                  f"资金净流出占成交额 {abs(flow_ratio):.1f}%，超过 {abs(rules['flow_outflow_ratio']):.0f}%，降级为观望",
                  f"net fund outflow is {abs(flow_ratio):.1f}% of turnover, above {abs(rules['flow_outflow_ratio']):.0f}%, downgraded to Watch")
        action = "watch"
    severe = inputs.get("severe_notice")
    if action in BULLISH_ACTIONS and severe:
        guardrails.append(tr(lang, f"近 30 天公告含「{severe['risk']}」（{severe['date']} {severe['title'][:40]}），不建议买入，降级为观望",
                             f"A notice in the past 30 days contains \"{severe['risk']}\" ({severe['date']} {severe['title'][:40]}); buying not advised, downgraded to Watch"))
        action = "watch"
    disagreement = inputs.get("disagreement")
    if disagreement and confidence == "高":
        guardrails.append(tr(lang, f"分析员观点分歧（{disagreement}），信心下调为中",
                             f"Analysts disagree ({disagreement}); confidence lowered to Medium"))
        confidence = "中"
    bullish_history = inputs.get("calibration_bullish") or {}
    if (action in BULLISH_ACTIONS and bullish_history.get("n", 0) >= MIN_CALIBRATION_SAMPLES
            and bullish_history.get("accuracy") is not None and bullish_history["accuracy"] < LOW_CALIBRATION_ACCURACY):
        lowered = {"高": "中", "中": "低"}.get(confidence, "低")
        guardrails.append(tr(lang, f"近 90 天看多诊断 3 日准确率仅 {bullish_history['accuracy']}%（{bullish_history['n']} 次），信心下调为{lowered}",
                             f"Bullish diagnoses in the past 90 days hit only {bullish_history['accuracy']}% at 3 days ({bullish_history['n']} runs); "
                             f"confidence lowered to {display(lang, lowered)}"))
        confidence = lowered
    previous = inputs.get("previous")
    if previous:
        prev_action, prev_score = previous.get("action"), float(previous.get("score") or 0)
        flipped_up = action in BULLISH_ACTIONS and prev_action in BEARISH_ACTIONS
        flipped_down = action in BEARISH_ACTIONS and prev_action in BULLISH_ACTIONS
        if (flipped_up or flipped_down) and abs(score - prev_score) < STABILITY_SCORE_DELTA:
            note = tr(lang, f"与 {previous.get('created_at')} 的诊断（{previous.get('action_label')}，{prev_score:.0f}分）方向相反，但评分变化不足 {STABILITY_SCORE_DELTA} 分",
                      f"Opposite direction to the {previous.get('created_at')} diagnosis ({display(lang, previous.get('action_label'))}, {prev_score:.0f} pts), "
                      f"but the score changed by less than {STABILITY_SCORE_DELTA} pts")
            if flipped_up:
                guardrails.append(note + tr(lang, "，暂按观望处理，避免反复", "; treated as Watch to avoid flip-flopping"))
                action = "watch"
            else:
                guardrails.append(note + tr(lang, "，风险优先，保留减仓/回避建议", "; risk first, keeping the reduce/avoid advice"))

    phase_ctx = inputs.get("phase")
    phase_decision: dict = {}
    if phase_ctx:
        action, confidence, phase_decision, phase_notes = market_phase.phase_guardrails(
            action, confidence, inputs.get("phase_decision"), phase_ctx, inputs.get("quote_trade_date") or "", lang,
            fetched_at=inputs.get('quote_fetched_at'))
        guardrails.extend(phase_notes)

    # 风格专属规则放在安全护栏之后，只作用于仍然看多的建议
    if action in BULLISH_ACTIONS and rules["low_confidence_blocks"] and confidence == "低":
        downgrade("", "", "信心为低，不建议买入，降级为观望", "confidence is Low; buying not advised, downgraded to Watch")
        action = "watch"
    if action in BULLISH_ACTIONS and rules["require_invalidation"] and not (
            str(inputs.get("invalidation") or "").strip() or inputs.get("stop_loss")):
        downgrade("", "", "买入建议缺少失效条件和止损价，降级为观望",
                  "the buy advice has neither an invalidation condition nor a stop-loss price, downgraded to Watch")
        action = "watch"
    if action in BULLISH_ACTIONS and rules["position_note"]:
        guardrails.append(tr(lang, CONSERVATIVE_POSITION_NOTE, CONSERVATIVE_POSITION_NOTE_EN))
    return action, confidence, guardrails, phase_decision
