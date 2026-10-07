"""
AI 诊断事后验证（参考 daily_stock_analysis 的 decision signal outcome）

对近 N 天的个股诊断，用诊断所依据行情日之后的日线检验结论：
- 参考价：诊断行情日的收盘价；1/3/5 日收益按之后第 1/3/5 个交易日收盘计算
- 方向：买入/加仓 看多，减仓/卖出/回避 看空，持有/观望/提醒 不判方向；看多上涨、看空下跌算判断正确
- 价格计划：看多且给了止损价和目标价的，看之后 5 个交易日先碰到目标价还是止损价（同一天都碰到按止损）
同一只股票同一行情日诊断多次时展示最后一次。收益后验由公共引擎版本化落库，保留旧 API 的 1/3/5 日展示字段。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from src import trading_calendar
from src.analyzers.decision import ACTION_LABELS, BULLISH_ACTIONS
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import StockDiagnosis
from src.services.stock_diagnosis import BEARISH_ACTIONS
from src.utils.stock_code import diagnosis_code

HORIZONS = (1, 3, 5)
SCORE_BANDS = ((70, "70 分以上"), (50, "50~69 分"), (0, "50 分以下"))


def direction_of(action: str) -> int:
    """1 看多，-1 看空，0 不判方向。"""
    if action in BULLISH_ACTIONS:
        return 1
    return -1 if action in BEARISH_ACTIONS else 0


def plan_result(bars: list, stop_loss: float | None, target: float | None) -> str:
    """之后 5 个交易日先到目标价还是止损价。"""
    if not stop_loss or not target:
        return ""
    for b in bars:
        hit_stop = b.low is not None and b.low <= stop_loss
        hit_target = b.high is not None and b.high >= target
        if hit_stop:
            return "止损先到"
        if hit_target:
            return "止盈先到"
    return "未触及" if len(bars) >= max(HORIZONS) else "进行中"


class DiagnosisOutcomeService:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")

    def evaluate(self, lookback_days: int = 60) -> dict[str, Any]:
        trading_calendar.load(self.db_path, refresh=False)
        since = datetime.now() - timedelta(days=lookback_days)
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(StockDiagnosis.id, StockDiagnosis.result_json).filter(StockDiagnosis.created_at >= since)
                .order_by(StockDiagnosis.created_at).all()
            )
            latest: dict[tuple[str, str], dict[str, Any]] = {}
            for report_id, raw in rows:
                try:
                    r = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if r.get("error") or not r.get("trade_date"):
                    continue
                r["diagnosis_id"] = report_id
                latest[(diagnosis_code(r["code"]), r["trade_date"])] = r  # 同一行情日只算最后一次
            details = [self._evaluate_one(session, r) for r in latest.values()]
        details.sort(key=lambda d: (d["trade_date"], d["code"]), reverse=True)
        return {"summary": self._summary(details), "details": details}

    def _evaluate_one(self, session, r: dict[str, Any]) -> dict[str, Any]:
        code, trade_date = diagnosis_code(r["code"]), r["trade_date"]
        action = r.get("action", "")
        direction = direction_of(action)
        from src.services.outcome_engine import OutcomeEngine
        engine = OutcomeEngine(self.config)
        outcomes = {o.horizon: o for o in engine.evaluate(session, "diagnosis", r["diagnosis_id"], code, trade_date, direction)}
        from src.utils.timestamps import quote_now
        from types import SimpleNamespace
        base, _, _ = engine.price_path(session, code, trade_date, quote_now())
        bars = [SimpleNamespace(**bar) for bar in engine.validated_bars[:max(HORIZONS)]]
        detail: dict[str, Any] = {
            "trade_date": trade_date, "created_at": r.get("created_at", ""), "code": code, "name": r.get("name", ""),
            "action": action, "action_label": r.get("action_label") or ACTION_LABELS.get(action, action),
            "score": r.get("score"), "direction": direction, "base_close": base,
        }
        for n in HORIZONS:
            outcome = outcomes[n]
            ret = outcome.return_pct if outcome.status == "evaluated" else None
            detail[f"r{n}"] = round(ret, 2) if ret is not None else None
            detail[f"hit{n}"] = outcome.hit if ret is not None and direction else None
        plan = r.get("battle_plan") or {}
        detail["plan"] = plan_result(bars, plan.get("stop_loss"), plan.get("target_price")) if direction > 0 else ""
        return detail

    @staticmethod
    def _summary(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for d in details:
            groups[("操作建议", d["action_label"])].append(d)
            score = d.get("score") or 0
            band = next(label for floor, label in SCORE_BANDS if score >= floor)
            groups[("评分区间", band)].append(d)
            groups[("全部", "全部")].append(d)
        result = []
        for (dimension, group), items in groups.items():
            row: dict[str, Any] = {"dimension": dimension, "group": group, "total": len(items),
                                   "evaluated": sum(1 for d in items if d["r1"] is not None)}
            for n in HORIZONS:
                values = [d[f"r{n}"] for d in items if d[f"r{n}"] is not None]
                hits = [d[f"hit{n}"] for d in items if d[f"hit{n}"] is not None]
                row[f"avg_{n}d"] = round(sum(values) / len(values), 2) if values else None
                row[f"accuracy_{n}d"] = round(sum(hits) / len(hits) * 100, 1) if hits else None
            plans = [d["plan"] for d in items if d["plan"] in ("止盈先到", "止损先到")]
            row["target_first_rate"] = round(plans.count("止盈先到") / len(plans) * 100, 1) if plans else None
            result.append(row)
        order = {"全部": 0, "操作建议": 1, "评分区间": 2}
        band_order = {label: i for i, (_, label) in enumerate(SCORE_BANDS)}
        result.sort(key=lambda r: (order[r["dimension"]], band_order.get(r["group"], 0), -r["total"]))
        return result


# ---------- 历史校准（供 AI 诊断使用） ----------

CALIBRATION_LOOKBACK_DAYS = 90
MIN_CALIBRATION_SAMPLES = 10
LOW_ACCURACY = 45.0
CALIBRATION_CACHE_MINUTES = 30
STOCK_HISTORY_LINES = 3
_calibration_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}


def calibration_stats(config: dict, now: datetime | None = None) -> dict[str, Any]:
    """近 90 天看多/看空诊断的 3 日方向准确率和每只股票的历史诊断结果，进程内缓存 30 分钟。
    {"看多": {"n": 已验证数, "accuracy": %}, "看空": {...}, "details": [...]}"""
    now = now or datetime.now()
    db_path = config.get("database", {}).get("sqlite_path", "data/quant.db")
    cached = _calibration_cache.get(db_path)
    if cached and now - cached[0] < timedelta(minutes=CALIBRATION_CACHE_MINUTES):
        return cached[1]
    details = DiagnosisOutcomeService(config).evaluate(CALIBRATION_LOOKBACK_DAYS)["details"]
    stats: dict[str, Any] = {"details": details}
    for label, direction in (("看多", 1), ("看空", -1)):
        hits = [d["hit3"] for d in details if d["direction"] == direction and d["hit3"] is not None]
        stats[label] = {"n": len(hits), "accuracy": round(sum(hits) / len(hits) * 100, 1) if hits else None}
    _calibration_cache[db_path] = (now, stats)
    return stats


def calibration_text(stats: dict[str, Any], code: str) -> str:
    """交给 LLM 的「历史表现」一段；没有任何已验证的诊断时为空。"""
    parts = [f"{label}诊断 {stats[label]['n']} 次，3 日方向准确率 {stats[label]['accuracy']}%"
             for label in ("看多", "看空") if stats.get(label, {}).get("n")]
    own = [d for d in stats.get("details", []) if d["code"] == diagnosis_code(code) and d["r3"] is not None][:STOCK_HISTORY_LINES]
    if own:
        parts.append("本股最近：" + "；".join(f"{d['trade_date'][5:]} {d['action_label']}→3日{d['r3']:+.1f}%" for d in own))
    return ("【历史表现】近 90 天 AI 诊断：" + "；".join(parts)) if parts else ""


def reset_calibration_cache() -> None:
    _calibration_cache.clear()
