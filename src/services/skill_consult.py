"""
多策略会诊（参考 daily_stock_analysis 的 skills router / aggregator 与 skill_opinion_* 服务）

- select_skills：按诊断上下文的段落文本和大盘环境挑选最相关的 1~N 个策略（纯函数、确定性）
- consult：每个策略并发调用一次 LLM，给出 看多/中性/看空、评分、信心、理由
- consensus：按权重加权得出共识与是否分歧；section_text 生成交给决策员的「【策略会诊】」段落
- SkillOpinionService：观点落库，5 个交易日后用日线评估命中，按近 90 天命中率给策略调权重（0.8~1.2）
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from src import trading_calendar
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import SkillOpinion, StockDaily
from src.services.strategy_skills import DEFAULT_SKILL, Skill, get_skill, load_skills

STANCES = ("看多", "中性", "看空")
CONFIDENCES = ("高", "中", "低")
EVAL_HORIZON = 5            # 评估用的交易日数
MIN_WEIGHT_SAMPLES = 20     # 样本不足时权重恒为 1
OVERSOLD_DROP_PCT = 20.0    # 区间跌幅超过该值视为超跌
VOLUME_RATIO_MIN = 2.0
EVENT_KEYWORDS = ("重组", "中标", "订单", "合同", "回购", "增持")

CONSULT_PROMPT = """你是按「{display_name}」策略分析的 A 股交易员。请严格按下面的策略标准，根据给出的数据判断这只股票未来 1~5 个交易日的方向；只使用给出的数据，不要编造，数据不足时降低信心。

策略标准：
{instructions}

仅返回 JSON：
{{"stance": "看多/中性/看空", "score": 0到100的整数, "confidence": "高/中/低", "reason": "80 字以内"}}"""

_NUMBER = re.compile(r"量比\s*[:：=]?\s*(\d+(?:\.\d+)?)")
_CLOSE = re.compile(r"收\s*(\d+(?:\.\d+)?)")


def _is_general(skill: Skill) -> bool:
    return skill.name == "general" or skill.display_name == DEFAULT_SKILL or skill.name == DEFAULT_SKILL


def _volume_ratio(tech: str) -> float | None:
    match = _NUMBER.search(tech or "")
    return float(match.group(1)) if match else None


def _range_change_pct(walk: str) -> float | None:
    """【近期走势】段内首尾收盘价的区间涨跌幅（%）；解析不到返回 None。"""
    closes = [float(x) for x in _CLOSE.findall(walk or "")]
    if len(closes) < 2 or closes[0] <= 0:
        return None
    return (closes[-1] / closes[0] - 1) * 100


def select_skills(sections: dict[str, str], regime: str, max_skills: int = 2,
                  requested: list[str] | None = None) -> list[Skill]:
    """挑选参与会诊的策略；requested 非空时按名称解析，否则按上下文规则打分。"""
    max_skills = max(1, int(max_skills or 1))
    if requested:
        picked: list[Skill] = []
        for key in requested:
            skill = get_skill(key)
            if skill and all(skill.name != p.name for p in picked):
                picked.append(skill)
        return picked[:max_skills]

    candidates = [s for s in load_skills() if not _is_general(s)]
    by_name = {s.name: s for s in candidates}
    scores = {s.name: 0 for s in candidates}

    def add(name: str, value: int) -> None:
        if name in scores:
            scores[name] += value

    for s in candidates:
        if regime and regime in s.market_regimes:
            add(s.name, 2)
    sections = sections or {}
    limit_text = sections.get("近期涨停", "")
    if limit_text and "近期无涨停" not in limit_text and limit_text.replace("【近期涨停】", "").strip():
        for name, value in (("limit_up_relay", 3), ("dragon_head", 2), ("dragon_pullback", 1)):
            add(name, value)
    tech = sections.get("技术面", "")
    if "多头" in tech:
        add("bull_trend", 2)
        add("shrink_pullback", 1)
    ratio = _volume_ratio(tech)
    if ratio is not None and ratio >= VOLUME_RATIO_MIN:
        add("volume_breakout", 2)
    change = _range_change_pct(sections.get("近期走势", ""))
    if change is not None and change <= -OVERSOLD_DROP_PCT:
        add("oversold_rebound", 2)
        add("bottom_volume", 1)
    news = sections.get("相关资讯", "") + sections.get("近 30 天公告", "")
    if any(k in news for k in EVENT_KEYWORDS):
        add("event_driven", 1)

    ranked = sorted((s for s in candidates if scores[s.name] > 0), key=lambda s: (-scores[s.name], s.priority, s.display_name))
    if not ranked:
        ranked = [s for s in candidates if regime and regime in s.market_regimes]  # 已按 priority 排序
    return ranked[:max_skills] if ranked else []


def _normalize(skill: Skill, raw: Any, weight: float) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    stance = str(raw.get("stance", "")).strip()
    stance = next((v for v in STANCES if v in stance), "中性")
    try:
        score = max(0.0, min(100.0, float(raw.get("score", 50))))
    except (TypeError, ValueError):
        score = 50.0
    confidence = str(raw.get("confidence", "")).strip()
    return {"skill": skill.name, "display_name": skill.display_name, "stance": stance, "score": round(score),
            "confidence": confidence if confidence in CONFIDENCES else "中",
            "reason": str(raw.get("reason", "")).strip()[:120], "weight": weight}


def consult(llm, context_text: str, skills: list[Skill], weights: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """每个策略并发调用一次 LLM；单个失败或返回非法时跳过该策略。"""
    if not skills:
        return []
    weights = weights or {}

    def ask(skill: Skill) -> dict[str, Any] | None:
        try:
            raw = llm.chat_json(user_message=context_text,
                                system_message=CONSULT_PROMPT.format(display_name=skill.display_name, instructions=skill.instructions))
            return _normalize(skill, raw, float(weights.get(skill.name, 1.0)))
        except Exception as e:
            logger.warning(f"策略会诊失败 [{skill.display_name}]: {e}")
            return None

    with ThreadPoolExecutor(max_workers=len(skills), thread_name_prefix="consult") as pool:
        return [o for o in pool.map(ask, skills) if o]


def consensus(opinions: list[dict[str, Any]]) -> dict[str, Any]:
    """按权重加权的共识；空列表返回 {}。"""
    if not opinions:
        return {}
    total = sum(float(o.get("weight", 1.0)) for o in opinions)
    if total <= 0:
        total = float(len(opinions))
        avg = sum(float(o["score"]) for o in opinions) / total
    else:
        avg = sum(float(o["score"]) * float(o.get("weight", 1.0)) for o in opinions) / total
    avg = round(avg, 1)
    stance = "看多" if avg >= 60 else "看空" if avg <= 40 else "中性"
    stances = {o["stance"] for o in opinions}
    return {"stance": stance, "score": avg, "agreement": "分歧" if {"看多", "看空"} <= stances else "一致"}


def section_text(opinions: list[dict[str, Any]], cons: dict[str, Any]) -> str:
    lines = ["【策略会诊】"]
    for o in opinions:
        lines.append(f"- {o['display_name']}：{o['stance']} {o['score']}分（信心{o['confidence']}，权重{o['weight']:.2f}）；{o['reason'] or '无'}")
    if cons:
        lines.append(f"共识：{cons['stance']} {cons['score']}分，各策略{cons['agreement']}"
                     + ("（分歧时请说明采信哪一方，信心不要给「高」）" if cons["agreement"] == "分歧" else ""))
    return "\n".join(lines)


class SkillOpinionService:
    """策略观点的落库、后验评估与权重。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")

    def record(self, diagnosis_id: int | None, code: str, name: str, trade_date: str, opinions: list[dict[str, Any]]) -> int:
        if not opinions:
            return 0
        with get_db_session(self.db_path) as session:
            for o in opinions:
                session.add(SkillOpinion(
                    diagnosis_id=diagnosis_id, code=code, name=name, skill=o["skill"], stance=o["stance"], score=o["score"],
                    confidence=o.get("confidence"), weight=o.get("weight", 1.0), reason=o.get("reason"), trade_date=trade_date,
                ))
        return len(opinions)

    def _future_closes(self, session, code: str, trade_date: str, cache: dict) -> list[tuple[str, float]]:
        key = (code, trade_date)
        if key not in cache:
            rows = (session.query(StockDaily.trade_date, StockDaily.close)
                    .filter(StockDaily.code == code, StockDaily.trade_date > trade_date)
                    .order_by(StockDaily.trade_date).limit(EVAL_HORIZON + 10).all())
            valid = set(trading_calendar.trade_days_only([r[0] for r in rows]))
            cache[key] = [(d, float(c)) for d, c in rows if d in valid and c]
        return cache[key]

    def evaluate(self, now: datetime | None = None) -> dict[str, int]:
        """给 5 个交易日已过的观点补 ret_5d 与命中；看多涨、看空跌算命中，中性不判。"""
        now = now or datetime.now()
        cache: dict = {}
        count = 0
        with get_db_session(self.db_path) as session:
            rows = session.query(SkillOpinion).filter(SkillOpinion.ret_5d.is_(None), SkillOpinion.trade_date.isnot(None)).all()
            for row in rows:
                try:
                    base_row = session.query(StockDaily.close).filter(
                        StockDaily.code == row.code, StockDaily.trade_date == row.trade_date).first()
                    if not base_row or not base_row[0]:
                        continue
                    bars = self._future_closes(session, row.code, row.trade_date, cache)
                    if len(bars) < EVAL_HORIZON:
                        continue
                    ret = round((bars[EVAL_HORIZON - 1][1] / float(base_row[0]) - 1) * 100, 2)
                except Exception as e:
                    logger.warning(f"评估策略观点失败 [{row.code} #{row.id}]: {e}")
                    continue
                row.ret_5d = ret
                row.hit = (ret > 0) if row.stance == "看多" else (ret < 0) if row.stance == "看空" else None
                row.evaluated_at = now
                count += 1
        return {"evaluated": count}

    def _stats(self, days: int) -> dict[str, dict[str, Any]]:
        since = datetime.now() - timedelta(days=days)
        stats: dict[str, dict[str, Any]] = {}
        with get_db_session(self.db_path) as session:
            rows = session.query(SkillOpinion.skill, SkillOpinion.hit, SkillOpinion.ret_5d).filter(
                SkillOpinion.created_at >= since, SkillOpinion.hit.isnot(None)).all()
        for skill, hit, ret in rows:
            s = stats.setdefault(skill, {"samples": 0, "hits": 0, "rets": []})
            s["samples"] += 1
            s["hits"] += 1 if hit else 0
            if ret is not None:
                s["rets"].append(ret)
        return stats

    @staticmethod
    def _weight(samples: int, hit_rate: float) -> float:
        if samples < MIN_WEIGHT_SAMPLES:
            return 1.0
        return round(max(0.8, min(1.2, 1 + (hit_rate - 50) / 100)), 3)

    def weights(self, days: int = 90) -> dict[str, float]:
        return {k: self._weight(v["samples"], v["hits"] / v["samples"] * 100) for k, v in self._stats(days).items()}

    def performance(self, days: int = 90) -> list[dict[str, Any]]:
        display = {s.name: s.display_name for s in load_skills()}
        result = []
        for skill, v in self._stats(days).items():
            rate = v["hits"] / v["samples"] * 100
            result.append({
                "skill": skill, "display_name": display.get(skill, skill), "samples": v["samples"], "hits": v["hits"],
                "hit_rate": round(rate, 1), "avg_ret": round(sum(v["rets"]) / len(v["rets"]), 2) if v["rets"] else None,
                "weight": self._weight(v["samples"], rate),
            })
        return sorted(result, key=lambda r: (-r["samples"], r["skill"]))
