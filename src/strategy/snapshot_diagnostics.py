"""对调用者给出的有限标量快照逐条件检查，不补数、不联网、不执行字符串表达式。"""

import ast
import inspect
import math
from types import SimpleNamespace

from src.strategy.screening_rules import FIELDS, matches

UNKNOWN = object()
EXTRA_FIELDS = {"theme_heat", "limit_pct", "circ_mv"}


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or not snapshot or len(snapshot) > 50 or set(snapshot) - FIELDS - EXTRA_FIELDS:
        raise ValueError("快照必须是最多 50 个白名单数值字段的扁平对象")
    if any(value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1e18) for value in snapshot.values()):
        raise ValueError("快照只接受有限数值或 null，不接受嵌套对象、表达式或布尔值")
    return snapshot


def _evaluate(node, values, params, derived):
    def evaluate(part): return _evaluate(part, values, params, derived)
    if isinstance(node, ast.Constant): return node.value
    if isinstance(node, ast.Name): return derived.get(node.id, UNKNOWN)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "f":
        if node.attr == "is_limit_up":
            change = values.get("change_pct")
            return UNKNOWN if change is None else change >= values.get("limit_pct", 10) - .3
        if node.attr == "themes":
            return UNKNOWN if values.get("theme_heat") is None else bool(values["theme_heat"])
        return values.get(node.attr)
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "p": return params.get(evaluate(node.slice), UNKNOWN)
    if isinstance(node, (ast.Tuple, ast.List)): return [evaluate(part) for part in node.elts]
    if isinstance(node, ast.BoolOp):
        results = [evaluate(part) for part in node.values]
        known = [bool(value) for value in results if value is not UNKNOWN]
        if isinstance(node.op, ast.And) and False in known: return False
        if isinstance(node.op, ast.Or) and True in known: return True
        return UNKNOWN if any(value is UNKNOWN for value in results) else all(known) if isinstance(node.op, ast.And) else any(known)
    if isinstance(node, ast.UnaryOp):
        value = evaluate(node.operand)
        if value is UNKNOWN or value is None: return UNKNOWN
        return not value if isinstance(node.op, ast.Not) else -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = evaluate(node.left), evaluate(node.right)
        if left is UNKNOWN or right is UNKNOWN or left is None or right is None: return UNKNOWN
        if isinstance(node.op, ast.Add): return left + right
        if isinstance(node.op, ast.Sub): return left - right
        if isinstance(node.op, ast.Mult): return left * right
        if isinstance(node.op, ast.Div): return left / right if right else UNKNOWN
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "abs" and len(node.args) == 1:
        value = evaluate(node.args[0])
        return UNKNOWN if value is UNKNOWN or value is None else abs(value)
    if isinstance(node, ast.Compare):
        left = evaluate(node.left)
        for operator, part in zip(node.ops, node.comparators):
            right = evaluate(part)
            if left is UNKNOWN or right is UNKNOWN: return UNKNOWN
            if isinstance(operator, ast.Is): passed = left is right
            elif isinstance(operator, ast.IsNot): passed = left is not right
            elif isinstance(operator, ast.In): passed = left in right
            elif isinstance(operator, ast.NotIn): passed = left not in right
            elif left is None or right is None: return UNKNOWN
            else:
                passed = left < right if isinstance(operator, ast.Lt) else left <= right if isinstance(operator, ast.LtE) else left > right if isinstance(operator, ast.Gt) else left >= right if isinstance(operator, ast.GtE) else left == right if isinstance(operator, ast.Eq) else left != right
            if not passed: return False
            left = right
        return True
    return UNKNOWN


def diagnose(snapshot, screener, strategies=None):
    values = validate_snapshot(snapshot)
    allowed = {strategy.name for strategy in screener.strategies}
    if strategies and (len(strategies) > 30 or set(strategies) - allowed):
        raise ValueError("策略标识无效或超过 30 个")
    rules = {rule["name"]: rule for rule in [*screener.rule_definitions, *screener.profiles]}
    result = []
    for strategy in screener.strategies:
        if strategies and strategy.name not in strategies: continue
        checks, derived = [], {}
        definition = rules.get(strategy.name)
        if definition:
            for condition in definition.get("conditions", []):
                fields = [condition["field"]] + ([condition["ref"]] if "ref" in condition else [])
                missing = [name for name in fields if values.get(name) is None]
                checks.append({"condition": condition, "status": "missing" if missing else "passed" if matches(SimpleNamespace(**values), [condition]) else "failed", "inputs": {name: values.get(name) for name in fields}, "missing": missing})
        else:
            tree = ast.parse(inspect.getsource(strategy.rule))
            for statement in tree.body[0].body:
                if isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Name):
                    derived[statement.targets[0].id] = _evaluate(statement.value, values, strategy.params, derived)
                if not isinstance(statement, ast.If): continue
                outcome = _evaluate(statement.test, values, strategy.params, derived)
                fields = sorted({node.attr for node in ast.walk(statement.test) if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "f" and node.attr not in {"is_limit_up", "themes"}})
                missing = [name for name in fields if values.get(name) is None]
                predicates = []
                for node in ast.walk(statement.test):
                    if isinstance(node, ast.Compare):
                        value = _evaluate(node, values, strategy.params, derived)
                        predicates.append({"condition": ast.unparse(node), "value": None if value is UNKNOWN else bool(value)})
                checks.append({"condition": "reject_if " + ast.unparse(statement.test), "status": "missing" if outcome is UNKNOWN else "failed" if outcome else "passed", "inputs": {name: values.get(name) for name in fields}, "missing": missing, "predicates": predicates})
        # 评分条件同样只用调用者快照，未给出的字段保持 None。
        from src.strategy.screener import Features
        supplied = {key: None for key in FIELDS}
        supplied.update(values)
        for key in ("theme_heat", "limit_pct"): supplied.pop(key, None)
        f = Features(code="snapshot", name="调用者快照", **supplied, limit_pct=values.get("limit_pct") or 10)
        f.themes = {"supplied_theme": values["theme_heat"]} if values.get("theme_heat") else {}
        try: outcome = strategy.rule(f, strategy.params)
        except (TypeError, ZeroDivisionError): outcome = None
        missing_core = [name for name in ("close", "change_pct", "amount") if values.get(name) is None]
        missing_check = any(check["status"] == "missing" for check in checks)
        result.append({"strategy": strategy.name, "label": strategy.label, "checks": checks, "matched": None if missing_core or missing_check else bool(outcome), "score": outcome[0] if outcome else None, "reason": outcome[1] if outcome else None, "missing_core": missing_core})
    return {"mode": "supplied_snapshot", "network_used": False, "snapshot": values, "strategies": result}
