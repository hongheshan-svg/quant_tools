"""
模拟盘组合风险（参考 daily_stock_analysis 的 portfolio risk：集中度、回撤、止损距离）

- 总仓位：持仓市值 / 总资产，与大盘环境建议的最高仓位（risk.market_regime_position 的系数）比较
- 个股集中度：单只持仓市值占总资产超过 portfolio_risk.single_max_pct（默认 30%）时提示
- 行业集中度：按股票最近一次涨停时的所属行业归类（没有涨停记录的归「未知」），同一行业超过 sector_max_pct（默认 50%）时提示
- 止损距离：现价距止损价不足 near_stop_pct（默认 3%）时提示，已跌破的单独提示
- 回撤：按成交记录和每日收盘价重放账户净值，给出最大回撤和当前回撤
account="real:<账户名>" 只看该实盘账户。
account="real" 时改为实盘记账（RealPortfolioService）的持仓和流水；实盘没有设置可用资金时不比较总仓位，
回撤按「现在没有现金」反推期初资金计算。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from loguru import logger

from src import trading_calendar
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import LimitUpStock, StockDaily, TradeFill, TradeOrder
from src.utils.stock_code import bare_code, code_candidates

UNKNOWN_SECTOR = "未知"


def replay_nav(fills: list[tuple], closes: dict[str, dict[str, float]], days: list[str], initial_cash: float) -> list[tuple[str, float]]:
    """按日重放账户净值：fills=[(code, side, price, quantity, filled_at)]，closes={code: {日期: 收盘价}}。
    当天没有收盘价的持仓按最近一次已知价格（没有则按成本）估值。"""
    cash, holdings, last_price = initial_cash, defaultdict(int), {}
    ordered = sorted(fills, key=lambda f: f[4] or datetime.min)
    idx, nav = 0, []
    for day in days:
        while idx < len(ordered) and (ordered[idx][4] is None or ordered[idx][4].strftime("%Y-%m-%d") <= day):
            code, side, price, qty, _ = ordered[idx]
            amount = float(price) * int(qty)
            if side == "buy":
                cash -= amount
                holdings[code] += int(qty)
            else:
                cash += amount
                holdings[code] -= int(qty)
            last_price.setdefault(code, float(price))
            idx += 1
        value = cash
        for code, qty in holdings.items():
            if qty:
                price = closes.get(code, {}).get(day)
                if price:
                    last_price[code] = price
                value += qty * last_price.get(code, 0.0)
        nav.append((day, value))
    return nav


def drawdowns(nav: list[tuple[str, float]]) -> dict[str, Any]:
    peak, worst, worst_day = 0.0, 0.0, ""
    for day, value in nav:
        peak = max(peak, value)
        dd = value / peak - 1 if peak else 0.0
        if dd < worst:
            worst, worst_day = dd, day
    current = nav[-1][1] / peak - 1 if nav and peak else 0.0
    return {"max_drawdown": round(worst * 100, 2), "max_drawdown_date": worst_day, "current_drawdown": round(current * 100, 2)}


class PortfolioRiskService:
    def __init__(self, config: dict | None = None, execution=None, account: str = "paper", real=None):
        self.config = config or load_config()
        self.account = account
        self._real = real
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        cfg = self.config.get("portfolio_risk") or {}
        self.single_max_pct = float(cfg.get("single_max_pct", 30))
        self.sector_max_pct = float(cfg.get("sector_max_pct", 50))
        self.near_stop_pct = float(cfg.get("near_stop_pct", 3))
        self._execution = execution

    @property
    def execution(self):
        if self._execution is None:
            from src.trading.execution_service import ExecutionService

            self._execution = ExecutionService(self.config)
        return self._execution

    @property
    def real(self):
        if self._real is None:
            from src.services.real_portfolio import RealPortfolioService

            account = self.account.split(":", 1)[1] if self.account.startswith("real:") else None
            self._real = RealPortfolioService(self.config, account=account)
        return self._real

    @property
    def _is_real(self) -> bool:
        return self.account == "real" or self.account.startswith("real:")

    def report(self) -> dict[str, Any]:
        snapshot = self.real.snapshot() if self._is_real else self.execution.get_trading_snapshot(order_limit=1)
        account, positions = snapshot["account"], snapshot["positions"]
        total = account["total_assets"] or 0.0
        warnings: list[str] = list(snapshot.get("warnings") or [])
        cash_known = account.get("cash_known", True)

        exposure = account["market_value"] / total * 100 if total else 0.0
        regime, suggested = self._regime_limit()
        if cash_known and suggested is not None and exposure > suggested + 1e-6:
            warnings.append(f"总仓位 {exposure:.0f}% 高于大盘「{regime}」环境建议的 {suggested:.0f}%")

        sectors = self._sectors([p["code"] for p in positions])
        rows = []
        for p in positions:
            weight = p["market_value"] / total * 100 if total else 0.0
            price, stop = p["market_price"], p.get("stop_loss") or 0.0
            stop_gap = (price / stop - 1) * 100 if stop and price else None
            status = ""
            if stop_gap is not None and stop_gap <= 0:
                status = "已跌破止损"
                warnings.append(f"{p['name'] or p['code']} 已跌破止损价 {stop:.2f}")
            elif stop_gap is not None and stop_gap < self.near_stop_pct:
                status = "接近止损"
                warnings.append(f"{p['name'] or p['code']} 距止损价仅 {stop_gap:.1f}%")
            if weight > self.single_max_pct:
                warnings.append(f"{p['name'] or p['code']} 占总资产 {weight:.0f}%，超过单只上限 {self.single_max_pct:.0f}%")
            cost = p["avg_cost"] * p["quantity"]
            rows.append({
                "code": p["code"], "name": p["name"], "sector": sectors.get(bare_code(p["code"]), UNKNOWN_SECTOR),
                "weight": round(weight, 1), "market_value": round(p["market_value"], 2),
                "pnl_pct": round(p["unrealized_pnl"] / cost * 100, 2) if cost else None,
                "stop_loss": stop or None, "stop_gap": round(stop_gap, 2) if stop_gap is not None else None, "status": status,
            })
        rows.sort(key=lambda r: -r["weight"])

        by_sector: dict[str, float] = defaultdict(float)
        for r in rows:
            by_sector[r["sector"]] += r["weight"]
        sector_rows = [{"sector": k, "weight": round(v, 1)} for k, v in sorted(by_sector.items(), key=lambda kv: -kv[1])]
        for s in sector_rows:
            if s["sector"] != UNKNOWN_SECTOR and s["weight"] > self.sector_max_pct:
                warnings.append(f"行业「{s['sector']}」合计占 {s['weight']:.0f}%，超过上限 {self.sector_max_pct:.0f}%")

        return {
            "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"), "account": self.account, "cash_known": cash_known,
            "realized_pnl": account.get("realized_pnl"),
            "total_assets": round(total, 2), "cash": round(account["cash"], 2), "exposure": round(exposure, 1),
            "regime": regime, "suggested_exposure": suggested,
            "positions": rows, "sectors": sector_rows, "drawdown": self._drawdown(), "warnings": warnings,
        }

    def _regime_limit(self) -> tuple[str, float | None]:
        try:
            from src.analyzers.market_regime import MarketRegimeAnalyzer

            regime = MarketRegimeAnalyzer(self.config).analyze()
            if regime.regime == "未知":
                return regime.regime, None
            return regime.regime, round(regime.position_factor * 100, 0)
        except Exception as e:
            logger.debug(f"组合风险读取大盘环境失败: {e}")
            return "未知", None

    def _sectors(self, codes: list[str]) -> dict[str, str]:
        """股票最近一次涨停时的所属行业。"""
        result: dict[str, str] = {}
        with get_db_session(self.db_path) as session:
            for code in codes:
                row = (
                    session.query(LimitUpStock.sector)
                    .filter(LimitUpStock.code.in_(code_candidates(code)), LimitUpStock.sector.isnot(None), LimitUpStock.sector != "")
                    .order_by(LimitUpStock.trade_date.desc()).first()
                )
                if row:
                    result[bare_code(code)] = row[0]
        return result

    def _fills_and_initial_cash(self) -> tuple[list[tuple], float]:
        if self._is_real:
            fills = self.real.fills()
            # 期初资金 = 现在的可用资金 + 全部买入 - 全部卖出（没有设置可用资金时按现在没有现金计算）
            flow = sum(price * qty * (1 if side == "buy" else -1) for _, side, price, qty, _ in fills)
            return fills, (self.real.cash() or 0.0) + flow
        broker = getattr(self.execution, "broker", None)
        initial_cash = float(getattr(broker, "_initial_cash", 0) or (self.config.get("trading") or {}).get("paper_initial_cash", 1_000_000))
        with get_db_session(self.db_path) as session:
            fills = [
                (bare_code(code), side, price, qty, filled_at) for code, side, price, qty, filled_at in
                session.query(TradeFill.code, TradeFill.side, TradeFill.price, TradeFill.quantity, TradeFill.filled_at)
                .join(TradeOrder, TradeOrder.id == TradeFill.order_id)
                .filter(TradeOrder.broker == getattr(broker, "name", "paper"))
                .all()
            ]
        return fills, initial_cash

    def _drawdown(self) -> dict[str, Any]:
        fills, initial_cash = self._fills_and_initial_cash()
        with get_db_session(self.db_path) as session:
            if not fills:
                return {"max_drawdown": 0.0, "max_drawdown_date": "", "current_drawdown": 0.0, "days": 0}
            start = min(f[4] for f in fills if f[4]).strftime("%Y-%m-%d") if any(f[4] for f in fills) else ""
            codes = {f[0] for f in fills}
            closes: dict[str, dict[str, float]] = defaultdict(dict)
            rows = (
                session.query(StockDaily.code, StockDaily.trade_date, StockDaily.close)
                .filter(StockDaily.code.in_([v for c in codes for v in code_candidates(c)]), StockDaily.trade_date >= start,
                        StockDaily.close > 0)
                .all()
            )
        for code, day, close in rows:
            closes[bare_code(code)][day] = close
        days = sorted(trading_calendar.trade_days_only({d for c in closes.values() for d in c} | {start}))
        nav = replay_nav(fills, closes, days, initial_cash)
        return {**drawdowns(nav), "days": len(nav)}
