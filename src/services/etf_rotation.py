"""国内 ETF 双动量轮动：冻结候选池、前复权价格、明确截止日；只做研究回测。"""

from dataclasses import asdict
from datetime import date, datetime
import hashlib
import math
import re

import pandas as pd

from src import trading_calendar
from src.database.db import get_db_session
from src.database.models import FundDaily
from src.strategy.etf_rotation import RotationParams, run_backtest, compute_metrics, annual_returns, parameter_sweep, latest_ranking, is_rebalance_day, select_holdings, holdings_to_weights


def finite(value):
    return float(value) if value is not None and math.isfinite(float(value)) else None


class ETFRotationService:
    def __init__(self, config):
        self.config = config
        self.db_path = (config.get("database") or {}).get("sqlite_path", "data/quant.db")

    def settings(self):
        return dict(self.config.get("etf_rotation") or {})

    def _refresh(self, code, start, end):
        from src.collectors.source_chain import fetch_with_fallback
        from src.collectors.fund_data import fetch_fund_daily
        def akshare():
            import akshare as ak
            frame = ak.fund_etf_hist_em(symbol=code, period="daily", start_date=start.replace("-", ""), end_date=end.replace("-", ""), adjust="qfq")
            return [{"trade_date": str(row["日期"]), "close": float(row["收盘"]), "source": "东方财富ETF(前复权)", "price_adjustment": "forward"} for _, row in frame.iterrows()]
        fetched = fetch_with_fallback("ETF轮动前复权", [("东方财富ETF(前复权)", akshare), ("腾讯ETF(前复权)", lambda: fetch_fund_daily(code, days=2000))], timeout_seconds=20)
        bars = fetched.data
        if not bars:
            raise ValueError(f"{code} 无可用前复权历史")
        with get_db_session(self.db_path) as session:
            for bar in bars:
                if bar.get("price_adjustment") != "forward" or not start <= bar["trade_date"] <= end or not finite(bar["close"]) or bar["close"] <= 0:
                    continue
                row = session.query(FundDaily).filter(FundDaily.code == code, FundDaily.trade_date == bar["trade_date"]).first()
                if row is None:
                    row = FundDaily(code=code, trade_date=bar["trade_date"])
                    session.add(row)
                row.close, row.source, row.price_adjustment, row.updated_at = bar["close"], bar["source"], "forward", datetime.now()

    def run(self, overrides=None):
        cfg = {**self.settings(), **(overrides or {})}
        pool = list(dict.fromkeys(cfg.get("risk_assets") or []))
        safe = cfg.get("safe_asset") or None
        codes = pool + ([safe] if safe else [])
        if not pool or len(pool) > 30 or any(not re.fullmatch(r"[15]\d{5}", str(code)) for code in codes) or safe in pool:
            raise ValueError("候选池需为 1～30 只国内 ETF（6 位 1/5 开头代码）；防守 ETF 不得在风险池中")
        start, end = cfg.get("start", "2018-01-01"), cfg.get("end") or date.today().isoformat()
        date.fromisoformat(start)
        date.fromisoformat(end)
        if start > end or end > date.today().isoformat():
            raise ValueError("回测起止日期无效或结束日期在未来")
        from src.services.market_phase import current_phase
        expected = current_phase().get("effective_daily_bar_date")
        if expected and end > expected:
            end = expected
        params = RotationParams(**{key: cfg[key] for key in ("lookback_days", "rebalance", "top_n", "switch_buffer_pct", "cost_bps") if key in cfg})
        if not all(math.isfinite(float(value)) for value in asdict(params).values() if isinstance(value, (int, float))) or params.cost_bps >= 10000 or params.top_n > len(pool) or params.lookback_days > 1000:
            raise ValueError("轮动参数超出有效范围")
        trading_calendar.load(self.db_path, refresh=False)
        limitations = []
        if not trading_calendar.has_calendar_coverage(start, end):
            limitations.append("calendar_uses_weekday_fallback")
        if cfg.get("refresh"):
            for code in codes:
                self._refresh(code, start, end)
        with get_db_session(self.db_path) as session:
            records = session.query(FundDaily.code, FundDaily.trade_date, FundDaily.close, FundDaily.source).filter(FundDaily.code.in_(codes), FundDaily.trade_date >= start, FundDaily.trade_date <= end, FundDaily.price_adjustment == "forward").all()
        if not records:
            raise ValueError("没有可用前复权 ETF 历史；请先更新价格，未标记价格口径的旧记录不可用于轮动")
        frame = pd.DataFrame(records, columns=["code", "date", "close", "source"])
        observed_end = frame["date"].max()
        requested_last = end if trading_calendar.is_trade_day(end) else trading_calendar.prev_trade_day(end).isoformat()
        if observed_end < requested_last:
            limitations.append("history_does_not_reach_cutoff")
        index = pd.DatetimeIndex([day for day in pd.date_range(start, observed_end) if trading_calendar.is_trade_day(day.to_pydatetime())])
        closes = frame.pivot(index="date", columns="code", values="close")
        closes.index = pd.to_datetime(closes.index)
        closes = closes.reindex(index=index, columns=codes)
        missing = [code for code in codes if closes[code].notna().sum() < params.lookback_days + 2]
        if missing:
            limitations.append("insufficient_history:" + ",".join(missing))
        result = run_backtest(closes, pool, safe, params)
        if (result.equity.index[-1] - result.equity.index[0]).days / 365.25 < float(cfg.get("min_years", 8)):
            limitations.append("insufficient_backtest_span")
        as_of, ranking = latest_ranking(closes, pool, params)
        next_day = trading_calendar.next_trade_day(as_of.to_pydatetime())
        signal_due = is_rebalance_day(as_of, pd.Timestamp(next_day), params.rebalance)
        # 以首笔成交前的资本为基点，包含首笔交易费；基准从同一执行日开始计收益。
        equity = result.equity.copy()
        if result.trades:
            entry = result.trades[0].exec_date
            equity.loc[entry] = 1 - result.trades[0].turnover * params.cost_bps / 10000
            baseline = closes.index[closes.index < entry][-1]
            equity = pd.concat([pd.Series([1.0], index=[baseline]), equity])
            benchmark = pd.concat([pd.Series([1.0], index=[baseline]), result.benchmark_equity])
        else:
            benchmark = result.benchmark_equity
            limitations.append("no_executable_rebalance")
        chosen = select_holdings(ranking, result.final_holdings, params) if signal_due else result.final_holdings
        def metrics(series): return {key: finite(value) for key, value in compute_metrics(series).items()}
        digest = hashlib.sha256(closes.to_json(date_format="iso").encode()).hexdigest()
        return {"status": "partial" if limitations else "success", "limitations": limitations, "as_of": as_of.date().isoformat(),
                "parameters": {**asdict(params), "risk_assets": pool, "safe_asset": safe, "start": start, "end": end},
                "price_adjustment": "forward", "price_snapshot_hash": digest, "sources": sorted(set(frame["source"].dropna())),
                "metrics": metrics(equity), "benchmark_metrics": metrics(benchmark),
                "annual_returns": {str(year): finite(value) for year, value in annual_returns(equity).items()},
                "parameter_sweep": [{"lookback_days": lookback, **{key: finite(value) for key, value in values.items()}} for lookback, values in parameter_sweep(closes, pool, safe, params)],
                "curve": [{"date": day.date().isoformat(), "equity": finite(value), "benchmark": finite(benchmark.get(day))} for day, value in equity.items()],
                "trades": [{"signal_date": trade.signal_date.date().isoformat(), "execution_date": trade.exec_date.date().isoformat(), "from_weights": trade.from_weights, "to_weights": trade.to_weights, "turnover": trade.turnover} for trade in result.trades],
                "ranking": [{"code": code, "momentum": finite(value), "eligible": finite(value) is not None and value > 0} for code, value in ranking.items()],
                "current_weights": result.final_weights, "signal_due": signal_due,
                "next_action": {"signal_date": as_of.date().isoformat(), "execution_date": next_day.isoformat() if signal_due else None, "weights": holdings_to_weights(chosen, params.top_n, safe) if signal_due else result.final_weights},
                "note": "只做研究回测；缺失成交日价格时不买卖，停牌估值可沿用旧价。短历史和参数扫描不构成有效样本或盈利保证。"}
