"""实盘按原始价格和真实流水重放单位净值；日末出入金不改变投资收益。"""

from collections import defaultdict
import math

from src.utils.stock_code import bare_code


def ledger_nav(trades, actions, flows, anchors, closes, days, accounts):
    """公司行为开盘生效、成交按时间顺序、出入金日末计。锚点之前不反推资本。"""
    cash = defaultdict(float)
    holdings = defaultdict(int)
    events = defaultdict(list)
    known = set()
    issues = set()
    for a in actions:
        events[a.ex_date].append(("09:00:00", 0, "action", a))
    for t in trades:
        events[t.trade_date].append((t.trade_time or "15:00:00", 1, "trade", t))
    for a in anchors:
        events[a.as_of.strftime("%Y-%m-%d")].append((a.as_of.strftime("%H:%M:%S"), 2, "anchor", a))
    for f in flows:
        events[f.flow_date].append(("23:59:59", 3, "flow", f))
    previous, unit = None, 1.0
    nav, samples = [], []
    # 事件不能因调用方遗漏某个报价日而消失；仍严格限制在请求截止日内。
    if days:
        days = sorted(set(days) | {day for day in events if day <= max(days)})
    for day in days:
        external = 0.0
        reset = False
        for _, _, kind, item in sorted(events[day], key=lambda e: e[:2]):
            account = item.account or "默认"
            if kind == "flow":
                delta = item.amount * (1 if item.direction == "in" else -1)
                cash[account] += delta
                external += delta
                known.add(account)
            elif kind == "anchor":
                cash[account] = item.cash
                known.add(account)
                reset = True
            elif kind == "trade":
                key = (account, bare_code(item.code))
                amount = item.price * item.quantity
                if item.side == "buy":
                    holdings[key] += item.quantity
                    cash[account] -= amount + (item.fee or 0)
                else:
                    if item.quantity > holdings[key]:
                        issues.add("incomplete_trade_ledger")
                    holdings[key] -= item.quantity
                    cash[account] += amount - (item.fee or 0)
            else:
                key = (account, bare_code(item.code))
                if holdings[key] <= 0:
                    issues.add("corporate_action_without_holding")
                    continue
                if item.action == "bonus":
                    holdings[key] += item.shares or 0
                elif item.action == "dividend":
                    cash[account] += item.cash or 0
                elif item.action == "tax":
                    cash[account] -= item.cash or 0
        value = sum(cash.values())
        valid = set(accounts) <= known
        for (_, code), qty in holdings.items():
            if not qty:
                continue
            price = closes.get(code, {}).get(day)
            if price is None or price <= 0 or not math.isfinite(price):
                valid = False
                issues.add("missing_historical_price")
            else:
                value += qty * price
        if not valid:
            previous = None
            continue
        # 首个已知资金锚点重新开始可验证区间；后续重新设置资金亦重新开始，不伪造历史收益。
        if reset:
            nav, samples = [], []
            unit, previous = 1.0, None
            issues.discard('missing_historical_price')
        if previous is not None:
            before_flow = value - external
            if previous <= 0 or before_flow < 0:
                issues.add("invalid_capital_base")
                previous = None
                continue
            unit *= before_flow / previous
        nav.append((day, unit))
        samples.append({"date": day, "assets": round(value, 4), "external_flow": external, "unit_nav": unit})
        previous = value
    if not (set(accounts) <= known):
        issues.add("unknown_initial_capital")
    status = "available" if len(nav) >= 2 and not issues else "partial" if nav else "unavailable"
    if len(nav) < 2:
        issues.add("insufficient_valuation_points")
    return nav, {"status": status, "limitations": sorted(issues), "valuation_points": len(nav), "method": "cashflow_adjusted_unit_nav", "samples": samples}


def real_drawdown(real, closes, days):
    from src.database.db import get_db_session
    from src.database.models import RealCash
    from src.services.real_portfolio import _account_filter
    with get_db_session(real.db_path) as session:
        anchors = [a for account in real._names() for a in session.query(RealCash).filter(_account_filter(RealCash, account)).all()]
        session.expunge_all()
    return ledger_nav(real._ordered_trades(), real._ordered_actions(), [f for a in real._names() for f in real._flows(a)], anchors, closes, days, real._names())
