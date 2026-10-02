"""策略综合与确定性审议：保留少数意见，严重分歧降低信心。"""

from src.services.opinion_validity import valid_opinion
from src.services.skill_consult import consensus


def synthesize(opinions: list[dict], *, llm=None, context_pack: dict | None = None, config: dict | None = None, budget=None) -> dict:
    valid = [op for op in opinions if valid_opinion(op)]
    summary = consensus(valid)
    if len(valid) < 2:
        return {"version": "synthesis-v1", "status": "insufficient", "valid_count": len(valid),
                "consensus": summary, "conflicts": [], "deliberation": {"status": "skipped", "rounds": 0}}
    bullish = [op for op in valid if op["stance"] == "看多"]
    bearish = [op for op in valid if op["stance"] == "看空"]
    conflicts = []
    if bullish and bearish:
        severe = any(o.get("confidence") == "高" for o in bullish) and any(o.get("confidence") == "高" for o in bearish)
        conflicts.append({"type": "directional_opposition", "severity": "high" if severe else "medium",
                          "description": "策略对方向存在相反判断", "participants": [o.get("skill") for o in bullish + bearish]})
    if max(o["score"] for o in valid) - min(o["score"] for o in valid) >= 40:
        conflicts.append({"type": "score_dispersion", "severity": "medium", "description": "策略评分差异至少 40 分"})
    minority = [o for o in valid if o["stance"] != summary["stance"]]
    result = {"version": "synthesis-v1", "status": "ready", "valid_count": len(valid), "consensus": summary,
            "support": [o for o in valid if o["stance"] == summary["stance"]], "minority": minority,
            "conflicts": conflicts, "confidence_cap": "低" if conflicts else None,
            "deliberation": {"status": "completed" if conflicts else "skipped", "mode": "deterministic", "rounds": 1 if conflicts else 0,
                             "resolution_status": "unresolved" if conflicts else "not_needed", "minority_view_preserved": bool(minority),
                             "responses": [{"skill": o.get("skill"), "original_stance": o["stance"], "revised_stance": o["stance"],
                                            "revision": "unchanged", "reason": o.get("reason", "")} for o in valid] if conflicts else []}}
    if not conflicts or llm is None or not (config or {}).get("enabled", False):
        return result
    return deliberate(result, valid, llm, context_pack or {}, config, budget)


def deliberate(result: dict, original: list[dict], llm, context: dict, config: dict, budget=None) -> dict:
    import json
    import time
    from src.collectors.request_budget import bounded_call
    from src.utils.redaction import redact_text
    expires = time.monotonic() + min(config.get("timeout_seconds", 30), max(0, budget.remaining() - 30) if budget else 30)
    current, rounds = original, []
    for round_no in range(1, min(3, int(config.get("max_rounds", 2))) + 1):
        if time.monotonic() >= expires:
            break
        try:
            prompt = json.dumps({"context_pack": context, "original_opinions": original, "current_opinions": current, "conflicts": result["conflicts"]}, ensure_ascii=False)
            response = bounded_call(lambda: llm.chat_json(prompt, system_message='只根据证据审议分歧。保留每个 skill；返回 {"opinions":[{"skill":"...","stance":"看多/看空/中性","score":0到100,"confidence":"高/中/低","reason":"具体证据或维持原意见的原因"}]}。不得添加策略。', max_tokens=1800), expires - time.monotonic())
            revised = response.get("opinions") if isinstance(response, dict) else None
            skills = {o.get("skill") for o in original}
            if not isinstance(revised, list) or len(revised) != len(original) or any(not valid_opinion(o) or not o.get("reason") for o in revised) or {o.get("skill") for o in revised} != skills:
                raise ValueError("审议响应不完整或观点无效")
            current = revised
            rounds.append({"round": round_no, "opinions": revised})
            if not synthesize(current)["conflicts"]:
                break
        except Exception as error:
            result["deliberation"]["failure_reason"] = redact_text(error, 200)
            break
    review = synthesize(current)
    original_conflicts = result["conflicts"]
    result.update({"original_opinions": original, "revised_opinions": current, "consensus": review["consensus"],
                   "original_conflicts": original_conflicts, "conflicts": review["conflicts"], "confidence_cap": "低" if review["conflicts"] else None})
    result["deliberation"].update({"mode": "llm", "rounds": len(rounds), "round_history": rounds,
        "status": "completed" if rounds else "fallback", "resolution_status": "unresolved" if review["conflicts"] else "resolved",
        "minority_view_preserved": bool(result["minority"]), "original_conflicts": original_conflicts})
    return result
