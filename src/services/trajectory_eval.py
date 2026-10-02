"""离线工具轨迹指标；衡量执行契约，不能替代真实 LLM 研究质量评价。"""

import json


def evaluate_trajectory(turn: dict, expected: dict) -> dict:
    tools = turn.get("tools") or []
    requested = [tool["name"] for tool in tools]
    required = set(expected.get("required_tools", []))
    seen, duplicates, failures, scope_violations = set(), 0, 0, 0
    for tool in tools:
        key = (tool["name"], json.dumps(tool.get("args") or {}, sort_keys=True))
        duplicates += key in seen
        seen.add(key)
        result = str(tool.get("result") or "")
        failures += result.startswith(("工具执行失败", "工具被拒绝"))
        scope_violations += result.startswith("工具被拒绝：超出当前股票范围")
    coverage = len(required.intersection(requested)) / len(required) if required else 1.0
    elapsed_ms = (turn.get("run_log") or {}).get("total_ms")
    budget_ms = expected.get("max_ms")
    metrics = {"tool_coverage": coverage, "requested_calls": len(tools), "unique_calls": len(seen),
               "duplicate_calls": duplicates, "failed_calls": failures, "scope_violations": scope_violations,
               "elapsed_ms": elapsed_ms, "budget_observed": elapsed_ms is not None}
    metrics["passed"] = (coverage == 1 and duplicates <= expected.get("max_duplicates", 0)
                         and failures <= expected.get("max_failures", 0) and len(tools) <= expected.get("max_calls", 20)
                         and scope_violations == 0 and not turn.get("error") and bool(turn.get("answer"))
                         and (not budget_ms or elapsed_ms is not None and elapsed_ms <= budget_ms))
    return metrics
