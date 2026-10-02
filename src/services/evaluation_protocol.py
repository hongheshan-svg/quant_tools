"""区分研究方向验证、独立样本收益和真实资金组合；不混用不同口径。"""

from src.strategy.data_quality import finite_number, prices_comparable


def sample_protocol(engine_version: str) -> dict:
    return {"version": "evaluation-protocol-v1", "mode": "independent_sample_return", "engine_version": engine_version,
            "entry": "next_session_open", "exit": "fixed_horizon_close", "t_plus_one": True,
            "portfolio_nav": False, "fees_modeled": False, "slippage_modeled": False,
            "blocked_exit_modeled": False, "limits": ["样本复利和回撤不等于共享资金组合净值", "价格观察不能证明实际成交"]}


def benchmark_samples(session, dates: list[str]) -> dict:
    from src.database.models import FundDaily
    from src import trading_calendar
    rows = session.query(FundDaily).filter(FundDaily.code.in_(["sh000300", "000300.SH", "000300"])).all()
    by_date = {row.trade_date: row for row in sorted(rows, key=lambda r: r.id or 0)}
    returns = []
    for day in dates:
        expected = trading_calendar.next_trade_day(day).isoformat()
        exit_day = trading_calendar.next_trade_day(expected).isoformat()
        entry, exit_row = by_date.get(expected), by_date.get(exit_day)
        if entry is None or exit_row is None or not prices_comparable(entry, exit_row):
            continue
        opening, close = finite_number(entry.open), finite_number(exit_row.close)
        if opening is not None and close is not None and min(opening, close) > 0:
            returns.append((close / opening - 1) * 100)
    return {"name": "沪深300", "code": "sh000300", "mode": "same_entry_and_holding_horizon", "samples": len(returns),
            "status": "available" if len(returns) == len(dates) and dates else "partial" if returns else "missing",
            "avg_1d": round(sum(returns) / len(returns), 3) if returns else None,
            "missing": len(dates) - len(returns)}
