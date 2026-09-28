"""
实盘记账（参考 daily_stock_analysis 的 portfolio：流水 → 持仓快照，券商 CSV 导入）

只记账、不连券商、不下单：
- 成交流水：手动记一笔，或导入券商导出的交割单/成交记录（CSV / Excel，按常见列名识别：成交日期、证券代码、
  证券名称、买卖标志/业务名称、成交价格、成交数量、佣金/印花税/过户费等），非买卖的行（红利、转账等）跳过；
  有成交编号时按编号去重，没有时按「日期+时间+代码+方向+价格+数量+序号」去重，重复导入不会记两次
- 持仓：按移动平均成本计算（手续费计入成本），卖出按平均成本结算已实现盈亏；卖出数量超过持仓时只减到 0 并提示
- 可用资金：用户设置一次当前可用资金作为锚点，之后发生的成交自动增减；没有设置时总资产只按持仓市值计算
- 止损价、目标价可逐只设置，没设置时按 risk.stop_loss_pct / take_profit_pct 从成本计算
持仓会接入组合风险、盘中提醒（止损/接近止损/目标价）、个股诊断和 AI 问股。
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import RealCash, RealPositionPlan, RealTrade, StockDaily
from src.utils.stock_code import bare_code, code_candidates

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("成交日期", "发生日期", "交收日期", "交易日期", "日期"),
    "time": ("成交时间", "时间"),
    "code": ("证券代码", "股票代码", "代码"),
    "name": ("证券名称", "股票名称", "名称"),
    "side": ("买卖标志", "买卖方向", "操作", "业务名称", "摘要", "委托类别", "交易类别", "方向"),
    "price": ("成交价格", "成交均价", "成交价", "价格"),
    "quantity": ("成交数量", "成交股数", "发生数量", "数量"),
    "trade_id": ("成交编号", "合同编号", "委托编号"),
}
FEE_COLUMNS = ("手续费", "佣金", "印花税", "过户费", "其他费", "交易费", "规费", "经手费", "证管费")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _number(value: Any) -> float | None:
    match = _NUMBER.search(str(value or "").replace(",", ""))
    return float(match.group()) if match else None


def _date(value: Any) -> str:
    digits = re.findall(r"\d+", str(value or ""))
    if len(digits) == 1 and len(digits[0]) == 8:
        d = digits[0]
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    if len(digits) >= 3 and len(digits[0]) == 4:
        return f"{digits[0]}-{int(digits[1]):02d}-{int(digits[2]):02d}"
    return ""


def _time(value: Any) -> str:
    """成交时间统一为 HH:MM:SS（支持 09:31:05、093105、9:31），识别不了返回空字符串。"""
    text = str(value or "").strip()
    parts = re.findall(r"\d+", text)
    if len(parts) == 1 and len(parts[0]) in (5, 6):
        d = parts[0].zfill(6)
        parts = [d[:2], d[2:4], d[4:]]
    if len(parts) >= 2 and all(p.isdigit() for p in parts[:3]):
        h, m, sec = (int(x) for x in (parts + ["0"])[:3])
        if h < 24 and m < 60 and sec < 60:
            return f"{h:02d}:{m:02d}:{sec:02d}"
    return ""


def _when(trade_date: str, trade_time: str | None) -> datetime:
    """成交时刻；没有时间的按当天收盘 15:00 处理。"""
    return datetime.strptime(f"{trade_date} {trade_time or '15:00:00'}", "%Y-%m-%d %H:%M:%S")


def _side(value: Any) -> str:
    text = str(value or "")
    if "买" in text:
        return "buy"
    if "卖" in text:
        return "sell"
    return ""


def read_rows(path: str) -> list[list[str]]:
    """CSV / TXT（UTF-8 或 GBK，逗号或制表符分隔）或 Excel，读成二维字符串表。"""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        import pandas as pd

        frames = pd.read_excel(p, sheet_name=None, dtype=str, header=None)
        return [["" if v is None or str(v) == "nan" else str(v).strip() for v in row] for df in frames.values() for row in df.values.tolist()]
    raw = p.read_bytes()
    text = next((raw.decode(enc) for enc in ("utf-8-sig", "gbk") if _decodes(raw, enc)), raw.decode("utf-8", errors="ignore"))
    rows = []
    for line in text.splitlines():
        if line.strip():
            delimiter = "\t" if "\t" in line else ","
            rows.append([c.strip().strip('"').strip("=").strip('"') for c in line.split(delimiter)])
    return rows


def _decodes(raw: bytes, encoding: str) -> bool:
    try:
        raw.decode(encoding)
        return True
    except UnicodeDecodeError:
        return False


def parse_trade_rows(rows: list[list[str]]) -> tuple[list[dict[str, Any]], int, str]:
    """(成交列表, 跳过的行数, 错误说明)。按表头识别列，表头前的行（账户信息等）忽略。"""
    for i, header in enumerate(rows):
        cells = [c.strip() for c in header]
        index = {key: next((cells.index(a) for a in aliases if a in cells), None) for key, aliases in COLUMN_ALIASES.items()}
        if index["code"] is None or index["side"] is None or index["price"] is None or index["quantity"] is None:
            continue
        if index["date"] is None:
            return [], 0, "表格里没有成交日期列"
        fee_idx = [cells.index(c) for c in FEE_COLUMNS if c in cells]
        trades, skipped, seen = [], 0, defaultdict(int)
        for row in rows[i + 1:]:
            get = lambda key: row[index[key]] if index[key] is not None and index[key] < len(row) else ""  # noqa: E731
            code_match = re.search(r"\d{6}", get("code"))
            side, date = _side(get("side")), _date(get("date"))
            price, qty = _number(get("price")), _number(get("quantity"))
            if not code_match or not side or not date or not price or not qty:
                skipped += 1
                continue
            trade = {
                "trade_date": date, "trade_time": _time(get("time")), "code": code_match.group(), "name": get("name"),
                "side": side, "price": price, "quantity": int(abs(qty)),
                "fee": round(sum(abs(_number(row[j]) or 0) for j in fee_idx if j < len(row)), 2),
            }
            base = get("trade_id") or f"{date}|{trade['trade_time']}|{trade['code']}|{side}|{price}|{trade['quantity']}"
            seen[base] += 1
            trade["import_key"] = f"{base}#{seen[base]}"[:120]
            trades.append(trade)
        return trades, skipped, ""
    return [], 0, "没有找到表头（需要证券代码、买卖标志、成交价格、成交数量几列）"


class RealPortfolioService:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        risk = self.config.get("risk") or {}
        self.stop_loss_pct = float(risk.get("stop_loss_pct", -0.05))
        self.take_profit_pct = float(risk.get("take_profit_pct", 0.15))

    # ---- 流水 ----

    def add_trade(self, trade_date: str, code: str, side: str, price: float, quantity: int, fee: float = 0.0,
                  name: str = "", trade_time: str = "", note: str = "") -> dict[str, Any]:
        side = {"买入": "buy", "卖出": "sell"}.get(side, side)
        if side not in ("buy", "sell") or price <= 0 or quantity <= 0 or not _date(trade_date):
            return {"ok": False, "error": "请填写正确的日期、方向、价格和数量"}
        bare = bare_code(code)
        if not (len(bare) == 6 and bare.isdigit()):
            from src.services.watchlist import WatchlistService

            resolved = WatchlistService(self.config).resolve(code)
            if not resolved:
                return {"ok": False, "error": f"找不到股票「{code}」"}
            bare, name = resolved[0], name or resolved[1]
        if side == "sell":
            held = next((p["quantity"] for p in self.positions() if p["code"] == bare), 0)
            if quantity > held:
                return {"ok": False, "error": f"卖出 {quantity} 股超过当前持仓 {held} 股"}
        with get_db_session(self.db_path) as session:
            session.add(RealTrade(trade_date=_date(trade_date), trade_time=_time(trade_time), code=bare, name=name or self._name(bare),
                                  side=side, price=price, quantity=int(quantity), fee=fee or 0.0, note=note[:200], source="manual"))
        return {"ok": True}

    def delete_trade(self, trade_id: int) -> bool:
        with get_db_session(self.db_path) as session:
            return session.query(RealTrade).filter(RealTrade.id == trade_id).delete() > 0

    def import_file(self, path: str) -> dict[str, Any]:
        trades, skipped, error = parse_trade_rows(read_rows(path))
        if error:
            return {"added": 0, "duplicate": 0, "skipped": skipped, "error": error}
        added = duplicate = 0
        with get_db_session(self.db_path) as session:
            existing = {k for (k,) in session.query(RealTrade.import_key).filter(RealTrade.import_key.isnot(None)).all()}
            for t in trades:
                if t["import_key"] in existing:
                    duplicate += 1
                    continue
                session.add(RealTrade(**t, source="import"))
                existing.add(t["import_key"])
                added += 1
        logger.info(f"导入实盘流水：新增 {added}，重复 {duplicate}，跳过 {skipped}")
        return {"added": added, "duplicate": duplicate, "skipped": skipped, "error": ""}

    def trades(self, limit: int = 500) -> list[dict[str, Any]]:
        with get_db_session(self.db_path) as session:
            rows = (session.query(RealTrade).order_by(RealTrade.trade_date.desc(), RealTrade.trade_time.desc(), RealTrade.id.desc())
                    .limit(limit).all())
            return [{"id": r.id, "trade_date": r.trade_date, "trade_time": r.trade_time or "", "code": r.code, "name": r.name or "",
                     "side": r.side, "price": r.price, "quantity": r.quantity, "fee": r.fee or 0.0, "source": r.source,
                     "note": r.note or ""} for r in rows]

    # ---- 资金与计划 ----

    def set_cash(self, cash: float, as_of: datetime | None = None) -> None:
        with get_db_session(self.db_path) as session:
            session.add(RealCash(cash=float(cash), as_of=as_of or datetime.now()))

    def set_plan(self, code: str, stop_loss: float | None, target_price: float | None) -> None:
        bare = bare_code(code)
        with get_db_session(self.db_path) as session:
            plan = session.query(RealPositionPlan).filter(RealPositionPlan.code == bare).first() or RealPositionPlan(code=bare)
            plan.stop_loss, plan.target_price = stop_loss or None, target_price or None
            session.add(plan)

    # ---- 持仓 ----

    def _ordered_trades(self) -> list[RealTrade]:
        with get_db_session(self.db_path) as session:
            rows = session.query(RealTrade).order_by(RealTrade.trade_date, RealTrade.trade_time, RealTrade.id).all()
            session.expunge_all()
            return rows

    def _replay(self) -> tuple[dict[str, dict[str, Any]], float, list[str]]:
        """按时间顺序重放流水：持仓（数量、平均成本、名称）、已实现盈亏、提示。"""
        holdings: dict[str, dict[str, Any]] = {}
        realized, warnings = 0.0, []
        for t in self._ordered_trades():
            pos = holdings.setdefault(t.code, {"quantity": 0, "cost": 0.0, "name": t.name or "", "first_date": t.trade_date})
            pos["name"] = t.name or pos["name"]
            if t.side == "buy":
                pos["cost"] += t.price * t.quantity + (t.fee or 0)
                pos["quantity"] += t.quantity
                if pos["quantity"] == t.quantity:
                    pos["first_date"] = t.trade_date
            else:
                qty = min(t.quantity, pos["quantity"])
                if qty < t.quantity:
                    warnings.append(f"{t.trade_date} 卖出 {t.name or t.code} {t.quantity} 股超过持仓 {pos['quantity']} 股，缺少更早的买入记录")
                avg = pos["cost"] / pos["quantity"] if pos["quantity"] else 0.0
                realized += (t.price - avg) * qty - (t.fee or 0)
                pos["cost"] -= avg * qty
                pos["quantity"] -= qty
        return {c: p for c, p in holdings.items() if p["quantity"] > 0}, round(realized, 2), warnings

    def positions(self) -> list[dict[str, Any]]:
        holdings, _, _ = self._replay()
        plans = self._plans()
        result = []
        with get_db_session(self.db_path) as session:
            for code, pos in holdings.items():
                avg = pos["cost"] / pos["quantity"]
                price = (session.query(StockDaily.close).filter(StockDaily.code.in_(code_candidates(code)), StockDaily.close > 0)
                         .order_by(StockDaily.trade_date.desc()).limit(1).scalar()) or avg
                plan = plans.get(code, {})
                result.append({
                    "account": "real", "code": code, "name": pos["name"] or self._name(code), "quantity": pos["quantity"],
                    "available_quantity": pos["quantity"], "avg_cost": round(avg, 4), "market_price": price,
                    "market_value": price * pos["quantity"], "unrealized_pnl": (price - avg) * pos["quantity"],
                    "stop_loss": plan.get("stop_loss") or round(avg * (1 + self.stop_loss_pct), 2),
                    "target_price": plan.get("target_price") or round(avg * (1 + self.take_profit_pct), 2),
                    "first_date": pos["first_date"],
                })
        return sorted(result, key=lambda p: -p["market_value"])

    def snapshot(self) -> dict[str, Any]:
        """与 ExecutionService.get_trading_snapshot() 同样的结构，供组合风险等复用。"""
        positions = self.positions()
        _, realized, warnings = self._replay()
        market_value = sum(p["market_value"] for p in positions)
        cash = self.cash()
        if cash is None:
            warnings.append("还没有设置可用资金，总资产只按持仓市值计算")
        return {
            "account": {"broker": "real", "cash": cash or 0.0, "market_value": market_value, "total_assets": (cash or 0.0) + market_value,
                        "unrealized_pnl": sum(p["unrealized_pnl"] for p in positions), "realized_pnl": realized,
                        "cash_known": cash is not None},
            "positions": positions, "warnings": warnings,
        }

    def cash(self) -> float | None:
        """当前可用资金 = 最近一次设置的金额 + 之后成交带来的资金变化。"""
        with get_db_session(self.db_path) as session:
            anchor = session.query(RealCash).order_by(RealCash.as_of.desc(), RealCash.id.desc()).first()
            if anchor is None:
                return None
            cash, as_of = anchor.cash, anchor.as_of
        for t in self._ordered_trades():
            if _when(t.trade_date, t.trade_time) > as_of:
                amount = t.price * t.quantity
                cash += -(amount + (t.fee or 0)) if t.side == "buy" else amount - (t.fee or 0)
        return round(cash, 2)

    def fills(self) -> list[tuple[str, str, float, int, datetime]]:
        """(code, side, 含手续费的成交价, 数量, 成交时间)，供组合风险按净值重放计算回撤。"""
        result = []
        for t in self._ordered_trades():
            fee_per_share = (t.fee or 0) / t.quantity if t.quantity else 0
            price = t.price + fee_per_share if t.side == "buy" else t.price - fee_per_share
            result.append((t.code, t.side, price, t.quantity, _when(t.trade_date, t.trade_time)))
        return result

    def _plans(self) -> dict[str, dict[str, float | None]]:
        with get_db_session(self.db_path) as session:
            return {p.code: {"stop_loss": p.stop_loss, "target_price": p.target_price} for p in session.query(RealPositionPlan).all()}

    def _name(self, code: str) -> str:
        with get_db_session(self.db_path) as session:
            return (session.query(StockDaily.name).filter(StockDaily.code.in_(code_candidates(code)), StockDaily.name.isnot(None))
                    .order_by(StockDaily.trade_date.desc()).limit(1).scalar()) or ""
