"""诊断、决策信号和策略观点共用的交易日后验引擎。"""

from __future__ import annotations

import json
import math
from datetime import datetime

from src import trading_calendar
from src.database.db import get_db_session
from src.database.models import FundDaily, ResearchOutcome, StockDaily
from src.utils.stock_code import code_candidates, diagnosis_code

ENGINE_VERSION = "daily-return-v3"
HORIZONS = (1, 3, 5, 10)


class OutcomeEngine:
    def __init__(self, config: dict):
        self.db_path = (config.get("database") or {}).get("sqlite_path", "data/quant.db")
        cfg = config.get("evaluation") or {}
        self.neutral_band = float(cfg.get("neutral_band_pct", 0.5))
        if not math.isfinite(self.neutral_band) or not 0 <= self.neutral_band <= 100:
            raise ValueError("中性带必须是 0..100 的有限百分比")
        self.version = f"{ENGINE_VERSION}:neutral={self.neutral_band:g}"

    @staticmethod
    def _positive(value) -> bool:
        return value is not None and math.isfinite(float(value)) and float(value) > 0

    def price_path(self, session, code: str, trade_date: str, now: datetime) -> tuple:
        from src.services.data_freshness import completed_bar_date, incomplete_bar
        cutoff = completed_bar_date(now)
        self._price_quality, self._path_failure, self.validated_bars = {}, "", []
        canonical = diagnosis_code(code)
        models = (FundDaily,) if canonical.startswith(("sh", "sz", "bj")) else (StockDaily, FundDaily)
        for model in models:
            aliases = code_candidates(canonical) if model is StockDaily else [canonical]
            from src.strategy.data_quality import prices_comparable, row_priority
            bases = session.query(model).filter(model.code.in_(aliases), model.trade_date == trade_date).all()
            base = max(bases, key=row_priority) if bases else None
            if not base or not self._positive(base.close):
                continue
            if incomplete_bar(trade_date, base.updated_at, cutoff):
                return None, [], 'incomplete_base_bar'
            rows = session.query(model).filter(model.code.in_(aliases), model.trade_date > trade_date,
                                                                     model.trade_date <= cutoff).order_by(model.trade_date).limit(100).all()
            valid_dates = set(trading_calendar.trade_days_only([r.trade_date for r in rows]))
            # 每个交易日只有一个规范收盘价，不能把多个历史别名当成多个交易日。
            closes = {}
            selected = {}
            for row in rows:
                day, close = row.trade_date, row.close
                if day in valid_dates and self._positive(close):
                    if day not in selected or row_priority(row) > row_priority(selected[day]):
                        selected[day] = row
            previous = base
            for day, row in sorted(selected.items()):
                expected = trading_calendar.next_trade_day(datetime.strptime(previous.trade_date, '%Y-%m-%d').date()).isoformat()
                if day != expected:
                    self._path_failure = 'missing_trading_day'
                    break
                if incomplete_bar(day, row.updated_at, cutoff):
                    self._path_failure = 'incomplete_daily_bar'
                    break
                if not prices_comparable(previous, row):
                    self._path_failure = "incomparable_price_basis"
                    break
                closes[day] = float(row.close)
                self.validated_bars.append({'date': day, 'close': float(row.close), 'open': row.open,
                                            'high': row.high, 'low': row.low})
                previous = row
            self._price_quality = {"base_source": getattr(base, "source", None),
                                   "base_revision": getattr(base, "price_revision", None),
                                   "price_adjustment": getattr(base, "price_adjustment", None),
                                   "end_revision": getattr(previous, "price_revision", None)}
            return float(base.close), sorted(closes.items()), ""
        return None, [], "missing_base_price"

    def evaluate(self, session, owner_type: str, owner_id: int, code: str, trade_date: str,
                 direction: int, *, now: datetime | None = None, horizons=HORIZONS) -> list[ResearchOutcome]:
        from src.utils.timestamps import quote_now
        now = now or quote_now()
        saved = session.query(ResearchOutcome).filter_by(owner_type=owner_type, owner_id=owner_id, engine_version=self.version).all()
        if all(any(r.horizon == h and r.status == "evaluated" for r in saved) for h in horizons):
            return [r for r in saved if r.horizon in horizons]
        base, bars, reason = self.price_path(session, code, trade_date, now)
        results = []
        for horizon in horizons:
            row = session.query(ResearchOutcome).filter_by(owner_type=owner_type, owner_id=owner_id,
                                                           horizon=horizon, engine_version=self.version).first()
            if row is not None and row.status == "evaluated":
                results.append(row)
                continue
            if row is None:
                row = ResearchOutcome(owner_type=owner_type, owner_id=owner_id, horizon=horizon, engine_version=self.version)
                session.add(row)
            row.neutral_band_pct, row.base_date, row.base_price = self.neutral_band, trade_date, base
            row.evaluated_at = now
            end = bars[horizon - 1][0] if len(bars) >= horizon else now.strftime("%Y-%m-%d")
            row.data_quality_json = json.dumps({"source": "local_daily", "available_bars": len(bars), "required_bars": horizon,
                **getattr(self, "_price_quality", {}), "calendar_verified": trading_calendar.has_calendar_coverage(trade_date, end)})
            row.status, row.reason = ("unable", reason) if reason else ("pending", "insufficient_daily_bars")
            row.end_date = row.end_price = row.return_pct = row.hit = None
            if reason in {'missing_base_price', 'incomplete_base_bar'}:
                row.status = 'pending'
            if len(bars) < horizon and self._path_failure:
                row.status, row.reason = ('pending' if self._path_failure == 'incomplete_daily_bar' else 'unable'), self._path_failure
            if base and len(bars) >= horizon:
                # 不允许跳过缺失交易日拼凑 horizon；日历不完整时明确留下限制。
                expected = trade_date
                complete = True
                for day, _ in bars[:horizon]:
                    expected = trading_calendar.next_trade_day(datetime.strptime(expected, "%Y-%m-%d").date()).strftime("%Y-%m-%d")
                    if day != expected:
                        complete = False
                        break
                if complete:
                    row.end_date, row.end_price = bars[horizon - 1]
                    row.return_pct = round((row.end_price / base - 1) * 100, 4)
                    row.hit = row.return_pct > self.neutral_band if direction > 0 else row.return_pct < -self.neutral_band if direction < 0 else abs(row.return_pct) <= self.neutral_band
                    row.status, row.reason = "evaluated", ""
                else:
                    row.status, row.reason = "unable", "missing_trading_day"
            results.append(row)
        session.flush()
        return results

    def list(self, owner_type: str, owner_id: int) -> list[dict]:
        with get_db_session(self.db_path) as session:
            rows = session.query(ResearchOutcome).filter_by(owner_type=owner_type, owner_id=owner_id).order_by(ResearchOutcome.horizon, ResearchOutcome.id).all()
            return [{"horizon": r.horizon, "engine_version": r.engine_version, "status": r.status, "reason": r.reason,
                     "base_date": r.base_date, "end_date": r.end_date, "return_pct": r.return_pct, "hit": r.hit,
                     "neutral_band_pct": r.neutral_band_pct, "data_quality": json.loads(r.data_quality_json or "{}")} for r in rows]
