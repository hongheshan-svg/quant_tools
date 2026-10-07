"""多因子筛选、独立风险和组合约束；模型只在既有候选集合内补充排序。"""

from __future__ import annotations

import json
import math
from pathlib import Path
from datetime import datetime, timedelta

import yaml

from src.strategy.data_quality import finite_number, prices_comparable
from src.collectors.request_budget import bounded_call
from src.utils.redaction import redact_text

FACTORS = {"value", "liquidity", "momentum", "activity", "stability", "reversal", "size", "theme_heat", "quality"}
PIPELINE_VERSION = "screening-pipeline-v1"


def validate_pipeline(cfg: dict) -> None:
    for key in ("enabled", "financial_enrichment", "llm_rerank"):
        if key in cfg and not isinstance(cfg[key], bool):
            raise ValueError(f"screening.pipeline.{key} 必须为布尔值")
    bounds = {"financial_candidates": (0, 200), "financial_timeout_seconds": (1, 600), "llm_top_k": (1, 50),
              "llm_timeout_seconds": (1, 180), "post_analysis_top_k": (0, 10), "post_analysis_timeout_seconds": (1, 600),
              "candidate_context_timeout_seconds": (1, 180),
              "risk_max_penalty": (0, 100), "risk_veto_threshold": (1, 100), "chase_change_pct": (0, 30),
              "max_same_bucket": (1, 30), "concentration_penalty": (0, 30)}
    for key, (low, high) in bounds.items():
        if key in cfg and (finite_number(cfg[key]) is None or not low <= cfg[key] <= high or key in {"financial_candidates", "llm_top_k", "post_analysis_top_k", "max_same_bucket"} and not isinstance(cfg[key], int)):
            raise ValueError(f"screening.pipeline.{key} 应在 {low}–{high} 之间")


def clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def quality(bars: list, f, payload: dict | None) -> dict:
    flags = []
    if f.bars < 21:
        flags.append("历史不足21根")
    if f.bars < min(len(bars), 61):
        flags.append("价格口径中断")
    if any(not prices_comparable(a, b) for a, b in zip(bars, bars[1:])):
        flags.append("价格口径不连续")
    if f.vol_ratio is None:
        flags.append("成交额历史缺失")
    if f.close_pos is None:
        flags.append("高低价无效")
    if not payload or payload.get("status") in {"stale", "missing", "fetch_failed"}:
        flags.append("财务证据缺失或过期")
    if not any(getattr(bar, "source", None) for bar in bars):
        flags.append("价格来源未记录")
    return {"score": max(0, 100 - sum(25 if "口径" in flag else 15 if "历史不足" in flag else 10 for flag in flags)),
            "flags": flags, "bars": f.bars, "source": getattr(bars[-1], "source", None) if bars else None,
            "price_adjustment": getattr(bars[-1], "price_adjustment", None) if bars else None,
            "fetched_at": bars[-1].updated_at.isoformat() if bars and getattr(bars[-1], "updated_at", None) else None,
            "price_revision": getattr(bars[-1], "price_revision", None) if bars else None}


def factor_scores(f) -> dict[str, float | None]:
    # 缺失维度保留 None，不能把缺失盈利能力当成零增长。
    value = None if f.pe is None or f.pb is None or f.pe <= 0 or f.pb <= 0 else clamp(110 - f.pe * 1.3 - f.pb * 5)
    quality_score = None if f.roe is None else clamp(40 + f.roe * 2 + (f.profit_yoy or 0) * .3)
    return {
        "value": value, "quality": quality_score,
        "liquidity": clamp(35 + 20 * math.log10(max(f.amount / 3e7, 1))),
        "momentum": None if f.ret_20 is None else clamp(55 + f.ret_20 * 1.2 - max(f.change_pct - 5, 0) * 10),
        "activity": None if f.vol_ratio is None else clamp(100 - abs(f.vol_ratio - 2) * 18 - max((f.turnover or 0) - 12, 0) * 4),
        "stability": None if f.range_20 is None else clamp(100 - f.range_20 * 1.3 - abs(f.change_pct) * 2),
        "reversal": None if f.pullback_15 is None else clamp(90 - abs(f.pullback_15 + 10) * 4 - max(-f.change_pct - 5, 0) * 8),
        "size": None if not f.circ_mv else clamp(80 - abs(math.log10(f.circ_mv) - 10.5) * 20),
        "theme_heat": clamp(max(f.themes.values())) if f.themes else None,
    }


def weighted_score(factors: dict, weights: dict) -> tuple[float, float]:
    total = sum(weights.values())
    known = sum(weight for key, weight in weights.items() if factors.get(key) is not None)
    # 固定分母：缺失因子只给中性基准，另由覆盖率与质量扣分说明。
    score = sum((factors[key] if factors.get(key) is not None else 50) * weight for key, weight in weights.items()) / total
    return clamp(score), round(known / total, 3)


def load_profiles(path: str, include_disabled: bool = False) -> list[dict]:
    from src.strategy.screening_rules import matches, FIELDS
    target = Path(path)
    if not target.exists():
        target = Path(path + ".example")
    if not target.exists():
        raise ValueError("多因子策略文件不存在")
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"profiles"} or not isinstance(raw["profiles"], list):
        raise ValueError("多因子策略文件必须包含 profiles 列表")
    names = set()
    for p in raw["profiles"]:
        if not isinstance(p, dict) or set(p) - {"name", "label", "description", "enabled", "weights", "conditions", "regimes", "min_score"}:
            raise ValueError("多因子策略格式无效")
        if not isinstance(p.get("name"), str) or not p["name"] or p["name"] in names:
            raise ValueError("多因子策略名称无效或重复")
        names.add(p["name"])
        weights = p.get("weights")
        if not isinstance(weights, dict) or not weights or set(weights) - FACTORS or any(finite_number(v) is None or v < 0 for v in weights.values()) or sum(weights.values()) <= 0:
            raise ValueError("多因子权重必须为已知因子的非负有限数值，总和大于零")
        if not isinstance(p.get("enabled", True), bool) or not isinstance(p.get("label", p["name"]), str):
            raise ValueError("多因子策略 enabled/label 类型无效")
        if finite_number(p.get("min_score", 50)) is None or not 0 <= p.get("min_score", 50) <= 100:
            raise ValueError("多因子最低分应在0–100之间")
        if not isinstance(p.get("regimes", ["进攻", "均衡", "防守", "冰点"]), list) or any(r not in {"进攻", "均衡", "防守", "冰点"} for r in p.get("regimes", [])):
            raise ValueError("多因子大盘环境无效")
        conditions = p.get("conditions", [])
        if not isinstance(conditions, list) or any(not isinstance(c, dict) or set(c) != {"field", "op", "value"} or c["field"] not in FIELDS or c["op"] not in {"gt", "gte", "lt", "lte", "eq"} or finite_number(c["value"]) is None for c in conditions):
            raise ValueError("多因子硬过滤条件无效")
    return [p for p in raw["profiles"] if include_disabled or p.get("enabled", True)]


def profile_rule(f, definition: dict):
    from src.strategy.screening_rules import matches
    if not matches(f, definition.get("conditions", [])):
        return None
    score, coverage = weighted_score(factor_scores(f), definition["weights"])
    if score < definition.get("min_score", 50):
        return None
    return score, f"{definition.get('label', definition['name'])}多因子 {score:.1f} 分，因子覆盖 {coverage:.0%}"


def apply_risk(pick, f, config: dict) -> bool:
    points, flags = 0.0, []
    for condition, penalty, label in (
        (f.change_pct >= config.get("chase_change_pct", 8), 10, "单日追涨"),
        (f.change_pct <= -7, 15, "大幅下跌"),
        (f.vol_ratio is not None and f.vol_ratio >= 6, 10, "异常放量"),
        (f.turnover is not None and f.turnover >= 15, 10, "高换手"),
        (f.profit_yoy is not None and f.profit_yoy < -30, 12, "盈利显著下滑"),
        (f.pe is not None and f.pe > 100 or f.pb is not None and f.pb > 10, 8, "估值偏高"),
        (bool(pick.event_risks), min(20, len(pick.event_risks) * 8), "公告事件风险"),
        (pick.data_quality.get("score", 100) < 75, (100 - pick.data_quality.get("score", 100)) * .25, "数据证据不足"),
    ):
        if condition:
            points += penalty
            flags.append(label)
    pick.risk_penalty = round(min(points, config.get("risk_max_penalty", 35)), 2)
    pick.risk_flags = flags
    pick.risk_level = "high" if points >= 25 else "medium" if points >= 10 else "low"
    pick.score = clamp(pick.score - pick.risk_penalty)
    pick.excluded_by_risk = points >= config.get("risk_veto_threshold", 30)
    from src.collectors.stock_news import classify_notice
    if any(classify_notice(title)[1] for title in pick.event_risks):
        pick.excluded_by_risk = True
        pick.risk_level = "high"
        pick.risk_flags.append("重大公告风险否决")
    return not pick.excluded_by_risk


def diversify(picks: list, config: dict) -> list:
    counts = {}
    for p in sorted(picks, key=lambda p: (-p.score, p.code)):
        # 未知行业不合成一个桶，避免把所有缺失行业的股票都当成同业。
        bucket = p.industry or (p.themes[0] if p.themes else "")
        if not bucket:
            continue
        counts[bucket] = counts.get(bucket, 0) + 1
        excess = max(0, counts[bucket] - config.get("max_same_bucket", 3))
        p.portfolio_penalty = min(15, excess * config.get("concentration_penalty", 5))
        p.score = clamp(p.score - p.portfolio_penalty)
        if excess:
            p.risk_flags.append("题材/行业集中：" + bucket)
    return sorted(picks, key=lambda p: (not p.fits_regime, -p.score, p.code))


def rerank(picks: list, config: dict, llm, *, seconds: float = 40) -> tuple[list, dict]:
    top = picks[:int(config.get("llm_top_k", 15))]
    if not top:
        return picks, {"status": "empty"}
    prompt = json.dumps([p.to_dict() for p in top], ensure_ascii=False)
    try:
        payload = bounded_call(lambda: llm.chat_json(prompt,
            system_message='依据提供的证据比较候选，不能添加代码或编造缺失事实。返回 {"rankings":[{"code":"600000","score":0到100,"reason":"..."}]}。'), seconds)
        rows = payload.get("rankings") if isinstance(payload, dict) else None
        allowed = {p.code for p in top}
        if not isinstance(rows, list) or len(rows) != len(top) or any(not isinstance(r, dict) or r.get("code") not in allowed or finite_number(r.get("score")) is None or not 0 <= r["score"] <= 100 or not isinstance(r.get("reason"), str) for r in rows) or len({r["code"] for r in rows}) != len(top):
            raise ValueError("模型排序未完整覆盖候选或包含无效代码/分数")
        scores = {r["code"]: r for r in rows}
        for p in top:
            p.llm_score = scores[p.code]["score"]
            p.llm_reason = scores[p.code]["reason"][:500]
            # 风险和组合扣分不被模型排序抵消。
            p.score = clamp(.8 * p.screen_score + .2 * p.llm_score - p.risk_penalty)
        return sorted(picks, key=lambda p: (not p.fits_regime, -p.score, p.code)), {"status": "available", "candidate_count": len(top)}
    except Exception as e:
        return picks, {"status": "fallback", "reason": redact_text(e, 200), "candidate_count": len(top)}
