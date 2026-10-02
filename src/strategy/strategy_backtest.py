"""
策略选股历史回测（参考 daily_stock_analysis 的回测与 AlphaEvo 的策略验证）

对区间内每个交易日按当时能看到的数据重新选股（日线、涨停题材、主线、大盘环境都只用当天及以前的数据，
不读进程内的实时行情缓存，策略权重也不生效），再统计入选股票之后的表现：
- 入场：次日开盘价；缺价、停牌或一字涨停不假定成交
- 1/3/5 日收益：买入后再经过 1/3/5 个交易日，以收盘价观察退出，符合 T+1
- 次日涨停率、适配大盘环境时的次日平均收益
- 样本复利与回撤仅用于比较选股批次，未模拟共享资金、费用与卖出成交，不能当作账户收益
结果保存到 strategy_backtest 表。策略权重：至少 30 个有效样本、10 个独立选股日且成熟样本完整时，按持有 1 日平均收益与全部策略的差值
温和调整（每差 1 个百分点调 0.05，限制在 0.8~1.2），选股排序时乘到策略得分上。
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable

from loguru import logger

from src import trading_calendar
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import StockDaily, StrategyBacktest
from src.strategy.screener import BACKTEST_ENGINE_VERSION, LIMIT_TOLERANCE, StrategyScreener
from src.strategy.data_quality import equity_code, finite_number, prices_comparable, row_priority
from src.utils.stock_code import code_candidates, daily_limit_pct
from src.utils.redaction import redact_text

HORIZONS = (1, 3, 5)
MIN_UNIVERSE = 1000        # 当天行情少于该数量的日期不回测（不是全市场）
MIN_WEIGHT_SAMPLES = 30
MIN_WEIGHT_DAYS = 10
WEIGHT_PER_PCT = 0.05
WEIGHT_MIN, WEIGHT_MAX = 0.8, 1.2
CHUNK = 500


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _win_rate(values: list[float]) -> float | None:
    return round(sum(1 for v in values if v > 0) / len(values) * 100, 1) if values else None


def max_drawdown(daily_returns: list[float]) -> float:
    """按日收益（%）复利的净值曲线的最大回撤（%，负数）。"""
    nav = peak = 1.0
    worst = 0.0
    for r in daily_returns:
        nav *= 1 + r / 100
        peak = max(peak, nav)
        worst = min(worst, nav / peak - 1)
    return round(worst * 100, 2)


def strategy_weights(stats: list[dict[str, Any]]) -> dict[str, float]:
    """样本足够的策略按次日平均收益相对全体的差值调整权重；样本不足的不调整。"""
    eligible = [s for s in stats if s.get("evaluated", 0) >= MIN_WEIGHT_SAMPLES
                and s.get("evaluated_days", 0) >= MIN_WEIGHT_DAYS and s.get("unavailable", 0) == 0
                and finite_number(s.get("avg_1d")) is not None]
    total = sum(s["evaluated"] for s in eligible)
    if not total:
        return {}
    overall = sum(s["avg_1d"] * s["evaluated"] for s in eligible) / total
    weights = {}
    for s in eligible:
        weight = 1 + (s["avg_1d"] - overall) * WEIGHT_PER_PCT
        weights[s["strategy"]] = round(min(WEIGHT_MAX, max(WEIGHT_MIN, weight)), 2)
    return weights


class StrategyBacktester:
    def __init__(self, config: dict | None = None, min_universe: int | None = None):
        self.config = config or load_config()
        self.min_universe = min_universe if min_universe is not None else int((self.config.get("screening") or {}).get("minimum_universe", MIN_UNIVERSE))
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        # 回测要保留每个策略自己的前 N 只，不做合并后的总数截断
        screening = {**(self.config.get("screening") or {}), "max_total": 10_000, "minimum_universe": self.min_universe}
        self.screener = StrategyScreener({**self.config, "screening": screening})

    def trade_dates(self, start: str, end: str) -> tuple[list[str], int]:
        """区间内行情接近全市场的交易日，以及因行情不全跳过的天数。"""
        trading_calendar.load(self.db_path, refresh=False)
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(StockDaily.trade_date, StockDaily.code, StockDaily.close, StockDaily.change_pct, StockDaily.amount)
                .filter(StockDaily.trade_date >= start, StockDaily.trade_date <= end)
                .all()
            )
        counts = defaultdict(set)
        for row in rows:
            counts[row.trade_date]  # 记录只有坏行的日期，不能消失在跳过天数里
            code, close = equity_code(row.code), finite_number(row.close)
            if code and close is not None and close > 0 and finite_number(row.change_pct) is not None and finite_number(row.amount) is not None and row.amount >= 0:
                counts[row.trade_date].add(code)
        days = []
        day = datetime.strptime(start, "%Y-%m-%d")
        until = min(end, datetime.now().strftime("%Y-%m-%d"))
        while day.strftime("%Y-%m-%d") <= until:
            key = day.strftime("%Y-%m-%d")
            if trading_calendar.is_trade_day(key):
                days.append(key)
            day += timedelta(days=1)
        full = [d for d in days if counts.get(d) and len(counts[d]) >= self.min_universe]
        return full, len(days) - len(full)

    def run(self, days: int = 60, end: str | None = None, save: bool = True,
            progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
        """回测最近 days 个自然日（到 end 为止）。progress(已完成, 总数) 用于界面显示进度。"""
        started = time.monotonic()
        end = end or datetime.now().strftime("%Y-%m-%d")
        start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
        dates, skipped = self.trade_dates(start, end)
        labels = {s.name: s for s in self.screener.strategies}
        samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
        completed, failures = [], {}
        for i, trade_date in enumerate(dates, 1):
            try:
                result = self.screener.run(trade_date, save=False, point_in_time=True)
                if result.status != "success":
                    failures[trade_date] = "行情或历史数据覆盖不足"
                    continue
                forward = self._forward_bars(trade_date, {p.code for p in result.picks})
                for p in result.picks:
                    outcome = self._outcome(p.code, p.name, p.close, forward.get(p.code) or [], trade_date=trade_date)
                    for strategy in p.strategies:
                        fits = result.regime in ("", "未知") or result.regime in labels[strategy].regimes
                        samples[strategy].append({"date": trade_date, "fits": fits, **outcome})
                completed.append(trade_date)
            except Exception as e:
                failures[trade_date] = redact_text(e, 200)
                logger.warning(f"策略回测 {trade_date} 失败: {failures[trade_date]}")
            finally:
                if progress:
                    progress(i, len(dates))

        stats = [self._summarize(name, labels[name], samples.get(name, [])) for name in labels]
        calendar_verified = bool(dates and trading_calendar.has_calendar_coverage(dates[0], self._expected_days(dates[-1])[-1]))
        weights = strategy_weights(stats) if completed and not failures and not skipped and calendar_verified else {}
        for s in stats:
            s["weight"] = weights.get(s["strategy"], 1.0)
        report = {
            "start": dates[0] if dates else start, "end": dates[-1] if dates else end, "dates": len(completed),
            "skipped_dates": skipped, "elapsed": round(time.monotonic() - started, 1),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "strategies": stats, "weights": weights,
            "status": "success" if completed and not failures and not skipped else "partial",
            "attempted_dates": len(dates), "failed_dates": len(failures), "errors": failures,
            "engine_version": BACKTEST_ENGINE_VERSION, "strategy_signature": self.screener.strategy_signature(),
            "calendar_verified": calendar_verified,
            "methodology": "次日开盘观察入场，持有 1/3/5 个交易日后收盘观察退出；缺行情不顺延，一字涨停不假定买入。样本复利未模拟共享资金、费用及退出成交。",
        }
        from src.services.evaluation_protocol import sample_protocol, benchmark_samples
        report["evaluation_protocol"] = sample_protocol(BACKTEST_ENGINE_VERSION)
        with get_db_session(self.db_path) as session:
            report["benchmark"] = benchmark_samples(session, completed)
        if not dates:
            report["note"] = "区间内没有全市场行情，先运行 python scripts/fetch_history.py --mode daily --start-date <日期> 回补日线"
        elif failures or skipped:
            report["note"] = f"数据不完整：{len(failures)} 天选股失败，{skipped} 天行情覆盖不足；本次不更新排序权重"
        elif not calendar_verified:
            report["note"] = "正式交易日历未覆盖完整回测区间，暂按工作日观察；本次不更新排序权重"
        if save and completed:
            with get_db_session(self.db_path) as session:
                session.add(StrategyBacktest(start_date=report["start"], end_date=report["end"],
                                             result_json=json.dumps(report, ensure_ascii=False)))
        logger.info(f"策略回测完成：{report['start']}~{report['end']} 共 {len(dates)} 天，耗时 {report['elapsed']}s")
        return report

    def latest(self) -> dict[str, Any] | None:
        with get_db_session(self.db_path) as session:
            row = session.query(StrategyBacktest.result_json).order_by(StrategyBacktest.created_at.desc()).first()
        return json.loads(row[0]) if row else None

    # ---- 数据 ----

    def _forward_bars(self, trade_date: str, codes: set[str]) -> dict[str, list]:
        """固定的六个交易日槽位：缺失位置保留 None，不能压缩后冒充下一日。"""
        expected = self._expected_days(trade_date)
        end = min(expected[-1], datetime.now().strftime("%Y-%m-%d"))
        variants = [v for c in codes for v in code_candidates(c)]
        by_code: dict[str, dict[str, Any]] = defaultdict(dict)
        with get_db_session(self.db_path) as session:
            for i in range(0, len(variants), CHUNK):
                rows = (
                    session.query(StockDaily.code, StockDaily.trade_date, StockDaily.open, StockDaily.high, StockDaily.low,
                                  StockDaily.close, StockDaily.change_pct, StockDaily.volume, StockDaily.amount,
                                  StockDaily.price_adjustment, StockDaily.source, StockDaily.updated_at, StockDaily.id)
                    .filter(StockDaily.code.in_(variants[i:i + CHUNK]), StockDaily.trade_date > trade_date,
                            StockDaily.trade_date <= end)
                    .all()
                )
                for r in rows:
                    code = equity_code(r.code)
                    if code and (r.trade_date not in by_code[code] or row_priority(r) > row_priority(by_code[code][r.trade_date])):
                        by_code[code][r.trade_date] = r
        return {code: [days.get(d) for d in expected] for code, days in by_code.items()}

    @staticmethod
    def _expected_days(trade_date: str) -> list[str]:
        dates, day = [], trade_date
        for _ in range(max(HORIZONS) + 1):
            day = trading_calendar.next_trade_day(day).strftime("%Y-%m-%d")
            dates.append(day)
        return dates

    @staticmethod
    def _outcome(code: str, name: str, close: float, bars: list, *, trade_date: str) -> dict[str, Any]:
        expected = StrategyBacktester._expected_days(trade_date)
        matured = expected[1] <= datetime.now().strftime("%Y-%m-%d")
        outcome = {"matured": matured, "unavailable": matured}
        if not bars or bars[0] is None or bars[0].trade_date != expected[0]:
            return outcome
        first = bars[0]
        change = finite_number(first.change_pct)
        if change is not None:
            outcome["limit_up"] = change >= daily_limit_pct(code, name) * 100 - LIMIT_TOLERANCE
        entry, high, low = finite_number(first.open), finite_number(first.high), finite_number(first.low)
        if entry is None or entry <= 0 or high is None or low is None or not 0 < low <= entry <= high:
            return outcome
        if outcome.get("limit_up") and high - low < 0.005:
            return {**outcome, "entry_blocked": True, "unavailable": False}
        if any(finite_number(getattr(first, key, None)) == 0 for key in ("volume", "amount")):
            return {**outcome, "entry_blocked": True, "unavailable": False}
        for n in HORIZONS:
            window = bars[:n + 1]
            if (len(window) == n + 1 and all(bar is not None and bar.trade_date == expected[i]
                    and finite_number(bar.close) is not None and bar.close > 0 for i, bar in enumerate(window))
                    and all(prices_comparable(a, b) for a, b in zip(window, window[1:]))):
                outcome[f"r{n}"] = (window[-1].close / entry - 1) * 100
        outcome["unavailable"] = matured and "r1" not in outcome
        return outcome

    @staticmethod
    def _summarize(name: str, strategy, rows: list[dict[str, Any]]) -> dict[str, Any]:
        evaluated = [r for r in rows if "r1" in r]
        stats: dict[str, Any] = {
            "strategy": name, "label": strategy.label, "regimes": "/".join(strategy.regimes),
            "picks": len(rows), "days": len({r["date"] for r in rows}), "evaluated": len(evaluated),
            "evaluated_days": len({r["date"] for r in evaluated}),
            "unavailable": sum(bool(r.get("unavailable")) for r in rows),
            "entry_blocked": sum(bool(r.get("entry_blocked")) for r in rows),
            "pending": sum(not r.get("matured", True) for r in rows),
        }
        for n in HORIZONS:
            values = [r[f"r{n}"] for r in rows if f"r{n}" in r]
            stats[f"avg_{n}d"], stats[f"win_{n}d"] = _mean(values), _win_rate(values)
        observed = [r for r in rows if "limit_up" in r]
        stats["limit_up_rate"] = round(sum(1 for r in observed if r["limit_up"]) / len(observed) * 100, 1) if observed else None
        stats["avg_1d_fit"] = _mean([r["r1"] for r in evaluated if r["fits"]])
        daily: dict[str, list[float]] = defaultdict(list)
        for r in evaluated:
            daily[r["date"]].append(r["r1"])
        curve = [sum(v) / len(v) for _, v in sorted(daily.items())]
        nav = 1.0
        for r in curve:
            nav *= 1 + r / 100
        stats["total_return"] = round((nav - 1) * 100, 2) if curve else None
        stats["max_drawdown"] = max_drawdown(curve) if curve else None
        return stats
