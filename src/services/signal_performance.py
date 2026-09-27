"""
信号绩效回测
按信号验证日开盘价入场，统计 1/3/5 个交易日后的收益、胜率和涨停命中率，
并模拟止损止盈离场，按信号类型、来源和 AI 研判分组汇总。

规则：
- 验证日：盘前预测为目标交易日，其余信号为生成后的下一个交易日。
- 入场：信号在验证日开盘（9:30）前生成的，按验证日开盘价入场；盘中生成的按验证日收盘价入场，
  避免用信号生成之前的价格买入。
- N 日收益：入场后第 N 个交易日收盘价相对入场价的涨跌幅（开盘入场时入场当天算第 1 天）。
- 止损止盈：A股 T+1，从入场后的下一个交易日开始判断；开盘即越过止损/止盈价时按开盘价离场；
  同一根日线同时触及止损和止盈时无法判断先后，保守按止损处理；都未触及则在窗口末收盘离场。
  止损止盈价优先用信号自带的价格计划，没有时按 risk.stop_loss_pct / take_profit_pct 计算。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from src import trading_calendar
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import LimitUpStock, StockDaily, TradeSignal
from src.services.self_learning import parse_signal_reason, signal_evaluation_date

HORIZONS = (1, 3, 5)
DEFAULT_LOOKBACK_DAYS = 60
LONG_SIGNAL_TYPES = ("premarket", "buy")
SIGNAL_TYPE_LABELS = {"premarket": "AI涨停预测", "buy": "综合评分信号"}


@dataclass
class SignalEvaluation:
    signal_id: int
    signal_date: str
    eval_date: str
    code: str
    name: str
    signal_type: str
    source: str
    verdict: str
    status: str = "pending"            # completed / pending（前向行情不足）/ no_data（验证日无行情）
    entry_at: str = ""                 # open / close
    entry_price: float | None = None
    returns: dict[int, float] = field(default_factory=dict)   # 持有天数 -> 收益率 %
    hit_limit_up: bool | None = None   # 验证日是否涨停
    stop_price: float | None = None
    take_price: float | None = None
    exit_reason: str = ""              # stop_loss / take_profit / ambiguous_stop_loss / window_end
    simulated_return: float | None = None


def _pct(price: float, base: float) -> float:
    return round((price - base) / base * 100, 3)


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total * 100, 1) if total else None


def simulate_exit(bars: list[Any], stop: float | None, take: float | None, last_index: int) -> tuple[str, float | None]:
    """bars[0] 为入场日，按 T+1 在 bars[1..last_index] 内模拟止损止盈，返回 (离场方式, 离场价)。"""
    for bar in bars[1 : last_index + 1]:
        stop_hit = stop is not None and bar.low is not None and bar.low <= stop
        take_hit = take is not None and bar.high is not None and bar.high >= take
        if stop_hit:
            reason = "ambiguous_stop_loss" if take_hit else "stop_loss"
            gap_down = bar.open is not None and bar.open < stop
            return reason, (bar.open if gap_down else stop)
        if take_hit:
            gap_up = bar.open is not None and bar.open > take
            return "take_profit", (bar.open if gap_up else take)
    if len(bars) > last_index and bars[last_index].close:
        return "window_end", bars[last_index].close
    return "", None


class SignalPerformanceService:
    """信号绩效统计（只读，不落库）。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        risk_cfg = self.config.get("risk", {})
        self.stop_loss_pct = float(risk_cfg.get("stop_loss_pct", -0.05))
        self.take_profit_pct = float(risk_cfg.get("take_profit_pct", 0.15))

    def evaluate(self, lookback_days: int = DEFAULT_LOOKBACK_DAYS, as_of: str | None = None) -> dict[str, Any]:
        """返回 {"summary": 分组统计, "details": 每条信号的评估, "lookback_days", "as_of"}。"""
        trading_calendar.load(self.db_path, refresh=False)
        as_of = as_of or date.today().strftime("%Y-%m-%d")
        start = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        with get_db_session(self.db_path) as session:
            signals = (
                session.query(TradeSignal)
                .filter(
                    TradeSignal.signal_date >= start,
                    TradeSignal.signal_date <= as_of,
                    TradeSignal.signal_type.in_(LONG_SIGNAL_TYPES),
                )
                .order_by(TradeSignal.signal_date.desc(), TradeSignal.composite_score.desc())
                .all()
            )
            evaluations = [self._new_evaluation(sig) for sig in signals]
            bars_by_code = self._load_bars(session, evaluations)
            limit_up_keys = self._load_limit_up_keys(session, evaluations)
            for ev, sig in zip(evaluations, signals):
                self._evaluate_one(ev, sig, bars_by_code.get(ev.code, []), limit_up_keys, as_of)

        return {
            "as_of": as_of,
            "lookback_days": lookback_days,
            "summary": self.summarize(evaluations),
            "details": [asdict(ev) for ev in evaluations],
        }

    @staticmethod
    def _new_evaluation(sig: TradeSignal) -> SignalEvaluation:
        signal_type = (sig.signal_type or "").lower()
        source = parse_signal_reason(sig.reason or "")[0] if signal_type == "premarket" else "综合评分"
        return SignalEvaluation(
            signal_id=sig.id,
            signal_date=sig.signal_date,
            eval_date=signal_evaluation_date(signal_type, sig.signal_date),
            code=(sig.code or "").strip().lower().removeprefix("sh").removeprefix("sz").removeprefix("bj"),
            name=sig.name or "",
            signal_type=signal_type,
            source=source,
            verdict=(sig.ai_verdict or "").strip(),
        )

    @staticmethod
    def _load_bars(session, evaluations: list[SignalEvaluation]) -> dict[str, list[StockDaily]]:
        """一次性取出所有相关股票在最早验证日之后的日线，按代码分组（兼容带交易所前缀的代码）。"""
        if not evaluations:
            return {}
        codes = {ev.code for ev in evaluations}
        cands = codes | {f"{p}{c}" for c in codes for p in ("sh", "sz", "bj")}
        rows = (
            session.query(StockDaily)
            .filter(StockDaily.code.in_(sorted(cands)), StockDaily.trade_date >= min(ev.eval_date for ev in evaluations))
            .order_by(StockDaily.trade_date.asc())
            .all()
        )
        grouped: dict[str, dict[str, StockDaily]] = defaultdict(dict)
        for row in rows:
            grouped[row.code[-6:]].setdefault(row.trade_date, row)
        return {code: list(by_date.values()) for code, by_date in grouped.items()}

    @staticmethod
    def _load_limit_up_keys(session, evaluations: list[SignalEvaluation]) -> set[tuple[str, str]]:
        if not evaluations:
            return set()
        rows = (
            session.query(LimitUpStock.code, LimitUpStock.trade_date)
            .filter(LimitUpStock.trade_date.in_(sorted({ev.eval_date for ev in evaluations})))
            .all()
        )
        return {(code[-6:], trade_date) for code, trade_date in rows}

    def _evaluate_one(self, ev: SignalEvaluation, sig: TradeSignal, bars: list[StockDaily], limit_up_keys, as_of: str):
        if ev.eval_date > as_of:
            return  # 验证日还没到
        window = [b for b in bars if b.trade_date >= ev.eval_date]
        if not window or window[0].trade_date != ev.eval_date:
            ev.status = "no_data"
            return

        opened_at = datetime.strptime(f"{ev.eval_date} 09:30", "%Y-%m-%d %H:%M")
        at_open = sig.created_at is None or sig.created_at < opened_at
        # 开盘入场时入场当天算第 1 天；收盘入场时从下一个交易日算起
        offset = 0 if at_open else 1
        entry = float((window[0].open if at_open else window[0].close) or 0)
        if not entry:
            ev.status = "no_data"
            return
        ev.entry_at = "open" if at_open else "close"
        ev.entry_price = entry
        ev.hit_limit_up = (ev.code, ev.eval_date) in limit_up_keys
        for h in HORIZONS:
            idx = h - 1 + offset
            if len(window) > idx and window[idx].close:
                ev.returns[h] = _pct(float(window[idx].close), entry)

        ev.stop_price = float(sig.stop_loss_price) if sig.stop_loss_price else round(entry * (1 + self.stop_loss_pct), 3)
        ev.take_price = float(sig.target_price) if sig.target_price else round(entry * (1 + self.take_profit_pct), 3)
        last_index = max(HORIZONS) - 1 + offset
        reason, exit_price = simulate_exit(window, ev.stop_price, ev.take_price, last_index)
        if exit_price:
            ev.exit_reason = reason
            ev.simulated_return = _pct(float(exit_price), entry)
        ev.status = "completed" if ev.exit_reason else "pending"

    @staticmethod
    def summarize(evaluations: list[SignalEvaluation]) -> list[dict[str, Any]]:
        """按 全部 / 信号类型 / 来源 / AI研判 分组统计。"""
        groups: dict[tuple[str, str], list[SignalEvaluation]] = defaultdict(list)
        for ev in evaluations:
            groups[("全部", "全部信号")].append(ev)
            groups[("信号类型", SIGNAL_TYPE_LABELS.get(ev.signal_type, ev.signal_type))].append(ev)
            groups[("来源", ev.source)].append(ev)
            if ev.verdict:
                groups[("AI研判", ev.verdict)].append(ev)

        rows = []
        for (dimension, label), items in groups.items():
            entered = [ev for ev in items if ev.entry_price]
            simulated = [ev for ev in entered if ev.simulated_return is not None]
            row: dict[str, Any] = {
                "dimension": dimension,
                "group": label,
                "total": len(items),
                "evaluated": len(entered),
                "limit_up_rate": _rate(sum(1 for ev in entered if ev.hit_limit_up), len(entered)),
                "simulated_count": len(simulated),
                "simulated_avg": _average([ev.simulated_return for ev in simulated]),
                "stop_loss_rate": _rate(sum(1 for ev in simulated if "stop_loss" in ev.exit_reason), len(simulated)),
                "take_profit_rate": _rate(sum(1 for ev in simulated if ev.exit_reason == "take_profit"), len(simulated)),
            }
            for h in HORIZONS:
                rets = [ev.returns[h] for ev in entered if h in ev.returns]
                row[f"win_rate_{h}d"] = _rate(sum(1 for r in rets if r > 0), len(rets))
                row[f"avg_return_{h}d"] = _average(rets)
            rows.append(row)

        order = {"全部": 0, "信号类型": 1, "来源": 2, "AI研判": 3}
        rows.sort(key=lambda r: (order.get(r["dimension"], 9), -r["total"]))
        return rows
