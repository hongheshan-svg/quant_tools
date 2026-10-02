"""YAML 声明式选股规则，字段白名单与有限数值校验；不执行表达式。"""

import math
from pathlib import Path

import yaml
from src.strategy.data_quality import finite_number

FIELDS = {"close", "change_pct", "amount", "turnover", "bars", "ma5", "ma10", "ma20", "ma60", "vol_ratio",
          "high_20", "range_20", "ret_20", "pullback_15", "limit_ups_15", "close_pos", "pe", "pb", "roe", "profit_yoy", "revenue_yoy", "dividend_yield"}
OPERATORS = {"gt", "gte", "lt", "lte", "eq", "between"}
REGIMES = {"进攻", "均衡", "防守", "冰点"}


def matches(features, conditions: list[dict]) -> bool:
    for condition in conditions:
        value = finite_number(getattr(features, condition["field"], None))
        target = getattr(features, condition["ref"], None) if condition.get("ref") else condition.get("value")
        op = condition["op"]
        if op == "between":
            if not isinstance(target, (list, tuple)) or len(target) != 2:
                return False
            target = [finite_number(v) for v in target]
            if any(v is None for v in target):
                return False
        else:
            target = finite_number(target)
        if value is None or target is None:
            return False
        passed = (value > target if op == "gt" else value >= target if op == "gte" else value < target if op == "lt"
                  else value <= target if op == "lte" else value == target if op == "eq" else target[0] <= value <= target[1])
        if not passed:
            return False
    return True


def load_rules(path: str) -> list[dict]:
    target = Path(path)
    if not target.exists():
        target = Path(str(target) + ".example")
    if not target.exists():
        return []
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    payload = {} if payload is None else payload
    if not isinstance(payload, dict) or set(payload) - {"rules"} or not isinstance(payload.get("rules", []), list):
        raise ValueError("YAML 选股配置必须为包含 rules 列表的对象")
    rules = payload.get("rules", [])
    names, result = set(), []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {"name", "label", "description", "regimes", "score", "conditions", "enabled"}:
            raise ValueError("YAML 选股策略格式错误或包含未知键")
        name = rule.get("name", "")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("YAML 选股策略标识不能为空或重复")
        names.add(name)
        if not isinstance(rule.get("enabled", True), bool):
            raise ValueError(f"{name} enabled 必须为布尔值")
        for field in ("label", "description"):
            if field in rule and not isinstance(rule[field], str):
                raise ValueError(f"{name} {field} 必须为文本")
        regimes = rule.get("regimes", ["进攻", "均衡", "防守", "冰点"])
        if not isinstance(regimes, list) or not regimes or any(not isinstance(r, str) or r not in REGIMES for r in regimes):
            raise ValueError(f"{name} regimes 必须为有效大盘环境列表")
        conditions = rule.get("conditions") or []
        if not isinstance(conditions, list) or not conditions:
            raise ValueError(f"{name} 至少需要一个条件")
        score = rule.get("score", 60)
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError(f"{name} 评分必须在 0..100")
        for condition in conditions:
            if not isinstance(condition, dict) or set(condition) - {"field", "op", "ref", "value"}:
                raise ValueError(f"{name} 条件格式错误或包含未知键")
            if (not isinstance(condition.get("field"), str) or condition["field"] not in FIELDS
                    or not isinstance(condition.get("op"), str) or condition["op"] not in OPERATORS):
                raise ValueError(f"{name} 包含未知字段或操作符")
            if ("ref" in condition) == ("value" in condition):
                raise ValueError(f"{name} 条件必须指定 value 或 ref 之一")
            if "ref" in condition:
                if not isinstance(condition["ref"], str) or condition["ref"] not in FIELDS or condition["op"] == "between":
                    raise ValueError(f"{name} 引用字段无效")
            else:
                values = condition.get("value")
                if condition["op"] == "between":
                    if not isinstance(values, list) or len(values) != 2:
                        raise ValueError(f"{name} between 需要两个边界")
                else:
                    values = [values]
                if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
                    raise ValueError(f"{name} 条件必须使用有限数值")
                if condition["op"] == "between" and values[0] > values[1]:
                    raise ValueError(f"{name} 条件下限不能大于上限")
        if rule.get("enabled", True):
            result.append(rule)
    return result
