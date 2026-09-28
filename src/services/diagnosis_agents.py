"""
多智能体个股诊断（参考 daily_stock_analysis 的 AgentOrchestrator：技术面 → 情报 → 风险 → 决策）

- 分析员只看自己负责的数据块，各自给出 看多/中性/看空、评分、信心、要点和风险；多个分析员并发调用
- 分歧：同时出现看多和看空，或评分相差 25 分以上时视为有分歧，交给决策员参考，并在结果里注明
- 决策员沿用 StockDiagnosisService 的决策仪表盘提示词，拿到全部数据和分析员观点后给出最终结论
模式（diagnosis.mode）：single 一次调用；standard 技术面 + 情报 → 决策；full 技术面 + 情报 + 风险 → 决策
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger

ANALYSTS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "technical": ("技术面分析员", "走势、技术指标、筹码、资金流和涨停质量",
                  ("股票", "行情", "近期走势", "技术面", "筹码", "资金流", "近期涨停", "主线地位")),
    "intel": ("情报分析员", "新闻、公告、舆情、业绩、龙虎榜和题材催化",
              ("股票", "行情", "相关资讯", "近 30 天公告", "AI舆情", "业绩", "龙虎榜", "主线地位")),
    "risk": ("风险分析员", "大盘环境、资金出逃、公告和业绩风险、仓位与止损",
             ("股票", "行情", "大盘环境", "资金流", "技术面", "近 30 天公告", "业绩", "持仓", "数据完整度")),
}
MODES: dict[str, tuple[str, ...]] = {"standard": ("technical", "intel"), "full": ("technical", "intel", "risk")}
DISAGREEMENT_SCORE_GAP = 25
VIEWS = ("看多", "中性", "看空")

ANALYST_PROMPT = """你是 A 股短线交易团队的{role}，只从{focus}的角度，根据给出的数据判断这只股票未来 1~5 个交易日的方向。
只使用给出的数据，不要编造；数据不足时降低信心。

仅返回 JSON：
{{"view": "看多/中性/看空", "score": 0到100的整数, "confidence": "高/中/低", "key_points": ["要点，最多 3 条"], "risks": ["风险，最多 3 条"]}}"""

DECISION_ADDENDUM = """

你是决策员：上面的数据之后附有各分析员的观点。综合全部数据和分析员观点给出最终结论；
分析员有分歧时要说明你采信哪一方以及原因，分歧大时信心不要给「高」。"""

_SECTION = re.compile(r"^【(.+?)】")


def split_sections(text: str) -> dict[str, str]:
    """把诊断上下文按「【标题】」拆成 {标题: 整行}，第一行「股票：…」记为「股票」。"""
    sections: dict[str, str] = {}
    for line in (text or "").splitlines():
        match = _SECTION.match(line)
        if match:
            sections[match.group(1)] = line
        elif line.startswith("股票："):
            sections["股票"] = line
    return sections


def _as_list(value: Any, limit: int = 3) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if x][:limit]
    return [str(value)] if value else []


def _normalize(role_key: str, raw: dict | None) -> dict[str, Any]:
    label = ANALYSTS[role_key][0]
    if not raw:
        return {"role": role_key, "label": label, "error": "未返回结果"}
    view = str(raw.get("view", "")).strip()
    view = next((v for v in VIEWS if v in view), "中性")
    try:
        score = max(0, min(100, int(float(raw.get("score", 50)))))
    except (TypeError, ValueError):
        score = 50
    confidence = str(raw.get("confidence", "")).strip()
    return {"role": role_key, "label": label, "view": view, "score": score,
            "confidence": confidence if confidence in ("高", "中", "低") else "中",
            "key_points": _as_list(raw.get("key_points")), "risks": _as_list(raw.get("risks"))}


def run_analysts(llm, context_text: str, mode: str) -> list[dict[str, Any]]:
    """按模式并发调用各分析员；单个分析员失败不影响其他人（结果里带 error）。"""
    roles = MODES.get(mode, ())
    if not roles:
        return []
    sections = split_sections(context_text)

    def ask(role_key: str) -> dict[str, Any]:
        label, focus, keys = ANALYSTS[role_key]
        data = "\n".join(sections[k] for k in keys if k in sections)
        try:
            return _normalize(role_key, llm.chat_json(user_message=data, system_message=ANALYST_PROMPT.format(role=label, focus=focus)))
        except Exception as e:
            logger.warning(f"{label}调用失败: {e}")
            return {"role": role_key, "label": label, "error": str(e)[:100]}

    with ThreadPoolExecutor(max_workers=len(roles), thread_name_prefix="analyst") as pool:
        return list(pool.map(ask, roles))


def disagreement(opinions: list[dict[str, Any]]) -> str:
    valid = [o for o in opinions if not o.get("error")]
    if len(valid) < 2:
        return ""
    views = {o["view"] for o in valid}
    scores = [o["score"] for o in valid]
    parts = []
    if "看多" in views and "看空" in views:
        parts.append("、".join(f"{o['label'][:-3]}{o['view']}" for o in valid))
    if max(scores) - min(scores) >= DISAGREEMENT_SCORE_GAP:
        parts.append(f"评分相差 {max(scores) - min(scores)} 分")
    return "；".join(parts)


def opinions_text(opinions: list[dict[str, Any]], conflict: str) -> str:
    lines = ["【分析员观点】"]
    for o in opinions:
        if o.get("error"):
            lines.append(f"- {o['label']}：未能给出观点")
            continue
        lines.append(f"- {o['label']}：{o['view']} {o['score']}分（信心{o['confidence']}）；要点：{'；'.join(o['key_points']) or '无'}；"
                     f"风险：{'；'.join(o['risks']) or '无'}")
    lines.append(f"【分歧】{conflict}" if conflict else "【分歧】观点基本一致")
    return "\n".join(lines)
