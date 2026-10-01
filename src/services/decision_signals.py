"""
决策信号生命周期（对齐 daily_stock_analysis 的 DecisionSignal）

个股 / ETF / 指数诊断给出买入、加仓、减仓、卖出、回避时生成一条决策信号，带观察期（有效期）、失效条件和价格计划：
- 状态：active（观察中）→ invalidated（出现相反信号）/ replaced（被同方向新信号替代）/ expired（观察期结束）/
  hit_target（多头触及目标价）/ hit_stop（多头触及止损价）
- 后验评估 evaluate()：以诊断行情日收盘价为基准，统计之后 1/3/5 日收益和观察期内最大不利/有利波动
- 复盘 review()：某只标的历史信号的命中率和偏差，写进下次诊断的提示词

后验口径（百分比，相对基准收盘价）：
- 多头（buy/add）：max_adverse_pct 为观察期最低价的最大跌幅（≤0）；max_favorable_pct 为最高价的最大涨幅（≥0）
- 空头（reduce/sell/avoid）：max_adverse_pct 为观察期最高价的最大涨幅（≥0，正数表示不利幅度）；
  max_favorable_pct 为最低价的最大跌幅取绝对值（≥0）
- 空头不判断止损止盈
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from src import trading_calendar
from src.analyzers.decision import ACTION_LABELS
from src.config_loader import load_config
from src.services.decision_profile import PROFILE_LABELS, normalize_profile
from src.database.db import get_db_session
from src.database.models import DecisionSignal, FundDaily, StockDaily

DIRECTIONS = {"buy": 1, "add": 1, "reduce": -1, "sell": -1, "avoid": -1}
DEFAULT_HORIZON = 5
MAX_HORIZON = 20
FETCH_BARS = 30               # 基准日之后最多读取的日线根数（观察期最长 20）
MIN_REVIEW_SAMPLES = 3
BIAS_HIT_RATE = 40.0          # 同方向命中率低于该值（%）视为判断偏差
FEEDBACKS = ("useful", "not_useful")
TERMINAL_STATUSES = ("expired", "hit_target", "hit_stop")
STATUS_LABELS = {
    "active": "观察中", "invalidated": "已失效", "replaced": "已替代", "expired": "已到期",
    "hit_target": "触及目标", "hit_stop": "触及止损",
}


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


class DecisionSignalService:
    """决策信号的生成、评估、复盘与查询。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")

    # ---------- 生成 ----------

    def record_from_diagnosis(self, result: dict[str, Any], diagnosis_id: int | None = None,
                              profile: str | None = None) -> dict[str, Any] | None:
        """从诊断结果生成一条 active 信号；观望/持有、出错的诊断不生成。同代码同风格旧的 active 信号同时收口，不同风格互不影响。"""
        if not result or result.get("error"):
            return None
        action = str(result.get("action") or "")
        direction = DIRECTIONS.get(action)
        code = str(result.get("code") or "").strip().lower()
        if not direction or not code:
            return None
        try:
            horizon = int(float(result.get("horizon_days")))
        except (TypeError, ValueError):
            horizon = DEFAULT_HORIZON
        if not 1 <= horizon <= MAX_HORIZON:
            horizon = DEFAULT_HORIZON
        trade_date = str(result.get("trade_date") or datetime.now().strftime("%Y-%m-%d"))
        plan = result.get("battle_plan") or {}
        label = ACTION_LABELS.get(action, action)
        profile = normalize_profile(profile or result.get("decision_profile"))
        with get_db_session(self.db_path) as session:
            for old in session.query(DecisionSignal).filter(DecisionSignal.code == code, DecisionSignal.status == "active").all():
                if normalize_profile(old.profile or "balanced") != profile:
                    continue
                if DIRECTIONS.get(old.action) != direction:
                    old.status, old.status_reason = "invalidated", f"出现相反信号：{label}"
                else:
                    old.status, old.status_reason = "replaced", "被新信号替代"
            row = DecisionSignal(
                diagnosis_id=diagnosis_id, code=code, name=result.get("name") or "", action=action,
                score=result.get("score"), confidence=result.get("confidence") or None,
                entry_low=plan.get("buy_price"), entry_high=plan.get("buy_price"),
                stop_loss=plan.get("stop_loss"), target_price=plan.get("target_price"),
                horizon_days=horizon, invalidation=(str(result.get("invalidation") or "")[:200] or None),
                trade_date=trade_date, status="active", expires_on=self._expires_on(trade_date, horizon),
                profile=profile,
            )
            session.add(row)
            session.flush()
            return self._to_dict(row)

    def save_reassessed(self, diagnosis_id: int, profile: str) -> dict[str, Any]:
        """按指定风格重新评估一条诊断并保存为该风格的决策信号。
        返回 {"status": "created"/"existing"/"skipped"/"error", ...}；同诊断同风格已有信号时返回已有的。"""
        from src.services.stock_diagnosis import StockDiagnosisService

        profile = normalize_profile(profile)
        with get_db_session(self.db_path) as session:
            existing = (
                session.query(DecisionSignal).filter(DecisionSignal.diagnosis_id == diagnosis_id, DecisionSignal.profile == profile)
                .order_by(DecisionSignal.id.desc()).first()
            )
            if existing:
                return {"status": "existing", "signal": self._to_dict(existing)}
        service = StockDiagnosisService(self.config)
        reassessed = service.reassess(diagnosis_id, profile)
        if reassessed.get("error"):
            return {"status": "error", "reason": reassessed["error"]}
        action = reassessed["action"]
        if action not in DIRECTIONS:
            return {"status": "skipped", "reason": f"该风格下的建议为「{reassessed['action_label']}」，不生成决策信号"}
        with get_db_session(self.db_path) as session:
            from src.database.models import StockDiagnosis

            row = session.get(StockDiagnosis, diagnosis_id)
            try:
                saved = json.loads(row.result_json or "{}") if row else {}
            except ValueError:
                saved = {}
        result = {
            **saved, "code": reassessed["code"], "name": reassessed["name"], "action": action,
            "confidence": reassessed["confidence"], "decision_profile": profile,
            "trade_date": saved.get("trade_date") or "",
        }
        signal = self.record_from_diagnosis(result, diagnosis_id, profile)
        if not signal:
            return {"status": "skipped", "reason": "不是方向性建议"}
        return {"status": "created", "signal": signal}

    @staticmethod
    def _expires_on(trade_date: str, horizon: int) -> str | None:
        """trade_date 之后第 horizon 个交易日。"""
        try:
            day = datetime.strptime(trade_date[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
        for _ in range(horizon):
            day = trading_calendar.next_trade_day(day)
        return day.strftime("%Y-%m-%d")

    # ---------- 日线 ----------

    def _load_bars(self, code: str, trade_date: str, cache: dict | None = None) -> tuple[float | None, list[dict]]:
        """返回（基准日收盘价，基准日之后的交易日日线）；个股读 stock_daily，指数/ETF 读 fund_daily。"""
        key = (code, trade_date)
        if cache is not None and key in cache:
            return cache[key]
        prefixed = len(code) == 8 and code[:2] in ("sh", "sz", "bj")
        models = [FundDaily] if prefixed else [StockDaily, FundDaily]
        base, bars = None, []
        with get_db_session(self.db_path) as session:
            for model in models:
                base_row = session.query(model.close).filter(model.code == code, model.trade_date == trade_date).first()
                if not base_row or not base_row[0]:
                    continue
                rows = (
                    session.query(model.trade_date, model.open, model.high, model.low, model.close)
                    .filter(model.code == code, model.trade_date > trade_date)
                    .order_by(model.trade_date).limit(FETCH_BARS + 10).all()
                )
                base = float(base_row[0])
                valid = set(trading_calendar.trade_days_only([r[0] for r in rows]))
                bars = [
                    {"date": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4]}
                    for r in rows if r[0] in valid and r[4]
                ][:FETCH_BARS]
                break
        if cache is not None:
            cache[key] = (base, bars)
        return base, bars

    # ---------- 评估 ----------

    def evaluate(self, now: datetime | None = None) -> dict[str, int]:
        """评估所有 active 信号，以及已到期/已触发但 5 日收益尚未补齐的信号。"""
        now = now or datetime.now()
        today = now.strftime("%Y-%m-%d")
        summary = {"evaluated": 0, "expired": 0, "hit_target": 0, "hit_stop": 0}
        cache: dict = {}
        with get_db_session(self.db_path) as session:
            rows = session.query(DecisionSignal).filter(
                (DecisionSignal.status == "active")
                | (DecisionSignal.status.in_(TERMINAL_STATUSES) & DecisionSignal.ret_5d.is_(None))
            ).all()
            for row in rows:
                try:
                    changed = self._evaluate_one(row, today, cache)
                except Exception as e:
                    logger.warning(f"评估决策信号失败 [{row.code} #{row.id}]: {e}")
                    continue
                if changed is None:
                    continue
                row.evaluated_at = now
                summary["evaluated"] += 1
                if changed in summary:
                    summary[changed] += 1
        return summary

    def _evaluate_one(self, row: DecisionSignal, today: str, cache: dict) -> str | None:
        """更新一条信号的后验指标和状态；返回新状态（无状态变化返回 ""），没有基准行情返回 None。"""
        base, bars = self._load_bars(row.code, row.trade_date or "", cache)
        if not base:
            return None
        direction = DIRECTIONS.get(row.action, 1)
        horizon = row.horizon_days or DEFAULT_HORIZON
        for n, attr in ((1, "ret_1d"), (3, "ret_3d"), (5, "ret_5d")):
            setattr(row, attr, _round((bars[n - 1]["close"] / base - 1) * 100) if len(bars) >= n else None)
        window = bars[:horizon]
        lows = [b["low"] / base - 1 for b in window if b["low"]]
        highs = [b["high"] / base - 1 for b in window if b["high"]]
        if direction > 0:
            row.max_adverse_pct = _round(min(min(lows), 0) * 100) if lows else None
            row.max_favorable_pct = _round(max(max(highs), 0) * 100) if highs else None
        else:
            row.max_adverse_pct = _round(max(max(highs), 0) * 100) if highs else None
            row.max_favorable_pct = _round(-min(min(lows), 0) * 100) if lows else None
        if row.status != "active":
            return ""
        if direction > 0:
            for bar in window:
                if row.stop_loss and bar["low"] and bar["low"] <= row.stop_loss:
                    row.status, row.status_reason = "hit_stop", f"{bar['date']} 最低价 {bar['low']} 触及止损 {row.stop_loss}"
                    return "hit_stop"
                if row.target_price and bar["high"] and bar["high"] >= row.target_price:
                    row.status, row.status_reason = "hit_target", f"{bar['date']} 最高价 {bar['high']} 达到目标 {row.target_price}"
                    return "hit_target"
        if row.expires_on and today >= row.expires_on:
            row.status, row.status_reason = "expired", f"观察期 {horizon} 个交易日已结束"
            return "expired"
        return ""

    # ---------- 命中判断 ----------

    def is_hit(self, signal: Any) -> bool | None:
        """是否命中：多头触及目标或观察期末收益 > 0 为命中，触及止损为未命中；空头观察期末收益 < 0 为命中；数据不足为 None。
        观察期末收益取已存的 ret_1d/3d/5d 中不超过观察期的最长一档。"""
        get = signal.get if isinstance(signal, dict) else (lambda k, d=None: getattr(signal, k, d))
        direction = DIRECTIONS.get(get("action"))
        if not direction:
            return None
        status = get("status")
        if direction > 0:
            if status == "hit_stop":
                return False
            if status == "hit_target":
                return True
        horizon = get("horizon_days") or DEFAULT_HORIZON
        ret = None
        for n, key in ((5, "ret_5d"), (3, "ret_3d"), (1, "ret_1d")):   # 观察期末：不超过观察期的最长已有收益
            if n <= horizon and get(key) is not None:
                ret = get(key)
                break
        if ret is None:
            return None
        return ret > 0 if direction > 0 else ret < 0

    # ---------- 复盘与统计 ----------

    def review(self, code: str, limit: int = 20) -> dict[str, Any]:
        """某只标的最近已结束的信号复盘，供诊断提示词和个股页使用。"""
        code = (code or "").strip().lower()
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(DecisionSignal).filter(DecisionSignal.code == code, DecisionSignal.status != "active")
                .order_by(DecisionSignal.created_at.desc(), DecisionSignal.id.desc()).limit(limit).all()
            )
            signals = [self._to_dict(r) for r in rows]
        judged = [(s, self.is_hit(s)) for s in signals]
        judged = [(s, h) for s, h in judged if h is not None]
        samples = len(judged)
        hits = sum(1 for _, h in judged if h)
        adverse_long = [s["max_adverse_pct"] for s, _ in judged if DIRECTIONS[s["action"]] > 0 and s["max_adverse_pct"] is not None]
        out: dict[str, Any] = {
            "code": code, "samples": samples, "hits": hits,
            "hit_rate": round(hits / samples * 100, 1) if samples else None,
            "avg_ret": _avg([s["ret_5d"] for s, _ in judged if s["ret_5d"] is not None]),
            "avg_adverse": _avg(adverse_long), "bias": "", "text": "",
        }
        if samples < MIN_REVIEW_SAMPLES:
            out["text"] = f"样本不足（{samples} 条）"
            return out
        bias = "正常"
        for direction, label in ((1, "偏乐观"), (-1, "偏悲观")):
            group = [h for s, h in judged if DIRECTIONS[s["action"]] == direction]
            if len(group) >= MIN_REVIEW_SAMPLES and sum(group) / len(group) * 100 < BIAS_HIT_RATE:
                bias = label
                break
        out["bias"] = bias
        text = f"近 {samples} 次信号命中 {hits} 次（{hits / samples * 100:.0f}%）"
        if adverse_long:
            text += f"，看多后平均最大回撤 {_avg(adverse_long):.1f}%"
        if bias != "正常":
            text += f"，判断{bias}"
        out["text"] = text
        return out

    def stats(self, days: int = 90, profile: str | None = None) -> dict[str, Any]:
        """近 N 天信号的状态分布、各建议的命中率与后验表现；profile 按决策风格过滤（unknown 为旧数据）。"""
        since = datetime.now() - timedelta(days=days)
        with get_db_session(self.db_path) as session:
            query = session.query(DecisionSignal).filter(DecisionSignal.created_at >= since)
            rows = self._filter_profile(query, profile).all()
            signals = [self._to_dict(r) for r in rows]
        by_status: dict[str, int] = {}
        groups: dict[str, list[tuple[dict, bool | None]]] = {}
        for s in signals:
            by_status[s["status"]] = by_status.get(s["status"], 0) + 1
            groups.setdefault(s["action"], []).append((s, self.is_hit(s)))
        by_action = {}
        judged_all = 0
        hits_all = 0
        for action, items in groups.items():
            judged = [h for _, h in items if h is not None]
            hits = sum(1 for h in judged if h)
            judged_all += len(judged)
            hits_all += hits
            by_action[action] = {
                "count": len(items), "hits": hits,
                "hit_rate": round(hits / len(judged) * 100, 1) if judged else None,
                "avg_ret_5d": _avg([s["ret_5d"] for s, _ in items if s["ret_5d"] is not None]),
                "avg_adverse": _avg([s["max_adverse_pct"] for s, _ in items if s["max_adverse_pct"] is not None]),
            }
        return {
            "total": len(signals), "by_status": by_status, "by_action": by_action,
            "hit_rate": round(hits_all / judged_all * 100, 1) if judged_all else None,
            "avg_ret_5d": _avg([s["ret_5d"] for s in signals if s["ret_5d"] is not None]),
            "avg_adverse": _avg([s["max_adverse_pct"] for s in signals if s["max_adverse_pct"] is not None]),
        }

    # ---------- 查询与反馈 ----------

    def list(self, status: str | None = None, action: str | None = None, code: str | None = None,
             days: int = 90, limit: int = 50, offset: int = 0, profile: str | None = None) -> dict[str, Any]:
        with get_db_session(self.db_path) as session:
            query = session.query(DecisionSignal)
            if days:
                query = query.filter(DecisionSignal.created_at >= datetime.now() - timedelta(days=days))
            if status:
                query = query.filter(DecisionSignal.status == status)
            if action:
                query = query.filter(DecisionSignal.action == action)
            if code:
                query = query.filter(DecisionSignal.code == code.strip().lower())
            query = self._filter_profile(query, profile)
            total = query.count()
            rows = query.order_by(DecisionSignal.created_at.desc(), DecisionSignal.id.desc()).offset(offset).limit(limit).all()
            items = [self._to_dict(r) for r in rows]
        for item in items:
            item["hit"] = self.is_hit(item)
        return {"total": total, "items": items}

    @staticmethod
    def _filter_profile(query, profile: str | None):
        """按决策风格过滤：空不过滤，"unknown" 为旧数据（NULL）。"""
        if not profile:
            return query
        if profile == "unknown":
            return query.filter(DecisionSignal.profile.is_(None))
        return query.filter(DecisionSignal.profile == normalize_profile(profile))

    def get(self, signal_id: int) -> dict[str, Any] | None:
        with get_db_session(self.db_path) as session:
            row = session.get(DecisionSignal, signal_id)
            if not row:
                return None
            item = self._to_dict(row)
        item["hit"] = self.is_hit(item)
        return item

    def set_feedback(self, signal_id: int, feedback: str | None, note: str = "") -> bool:
        """记录用户反馈（useful / not_useful / None 清除）；信号不存在或反馈非法返回 False。"""
        if feedback not in (*FEEDBACKS, None, ""):
            return False
        with get_db_session(self.db_path) as session:
            row = session.get(DecisionSignal, signal_id)
            if not row:
                return False
            row.feedback = feedback or None
            row.feedback_note = (note or "")[:500] or None
        return True

    @staticmethod
    def _to_dict(row: DecisionSignal) -> dict[str, Any]:
        def fmt(dt: datetime | None) -> str | None:
            return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None

        return {
            "id": row.id, "diagnosis_id": row.diagnosis_id, "code": row.code, "name": row.name,
            "action": row.action, "action_label": ACTION_LABELS.get(row.action, row.action or ""),
            "score": row.score, "confidence": row.confidence,
            "entry_low": row.entry_low, "entry_high": row.entry_high,
            "stop_loss": row.stop_loss, "target_price": row.target_price,
            "horizon_days": row.horizon_days, "invalidation": row.invalidation, "trade_date": row.trade_date,
            "status": row.status, "status_label": STATUS_LABELS.get(row.status, row.status or ""),
            "status_reason": row.status_reason, "expires_on": row.expires_on,
            "ret_1d": row.ret_1d, "ret_3d": row.ret_3d, "ret_5d": row.ret_5d,
            "max_adverse_pct": row.max_adverse_pct, "max_favorable_pct": row.max_favorable_pct,
            "profile": row.profile, "profile_label": PROFILE_LABELS.get(row.profile or "", ""),
            "evaluated_at": fmt(row.evaluated_at), "feedback": row.feedback, "feedback_note": row.feedback_note,
            "created_at": fmt(row.created_at), "updated_at": fmt(row.updated_at),
        }
