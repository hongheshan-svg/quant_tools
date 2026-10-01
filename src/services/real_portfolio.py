"""
实盘记账（参考 daily_stock_analysis 的 portfolio：流水 → 持仓快照，券商 CSV 导入）

只记账、不连券商、不下单：
- 成交流水：手动记一笔，或导入券商导出的交割单/成交记录（CSV / Excel，按常见列名识别：成交日期、证券代码、
  证券名称、买卖标志/业务名称、成交价格、成交数量、佣金/印花税/过户费等），非买卖的行（红利、转账等）跳过；
  有成交编号时按编号去重，没有时按「日期+时间+代码+方向+价格+数量+序号」去重，重复导入不会记两次
- 持仓：按移动平均成本计算（手续费计入成本），卖出按平均成本结算已实现盈亏；卖出数量超过持仓时只减到 0 并提示
- 可用资金：用户设置一次当前可用资金作为锚点，之后发生的成交自动增减；没有设置时总资产只按持仓市值计算
- 止损价、目标价可逐只设置，没设置时按 risk.stop_loss_pct / take_profit_pct 从成本计算
多账户：每条流水、资金锚点、止损计划、公司行为都带 account 列（空表示默认账户「默认」）；
RealPortfolioService(account=None) 汇总全部账户，指定账户时只读写该账户。
持仓会接入组合风险、盘中提醒（止损/接近止损/目标价）、个股诊断和 AI 问股。
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import or_

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import RealAccount, RealCash, RealCashFlow, RealCorporateAction, RealPositionPlan, RealTrade, StockDaily
from src.utils.stock_code import bare_code, code_candidates

DEFAULT_ACCOUNT = "默认"
ALL_ACCOUNTS_LABEL = "全部"
ACCOUNT_TABLES = (RealTrade, RealCash, RealPositionPlan, RealCorporateAction, RealCashFlow)
FLOW_LABELS = {"in": "入金", "out": "出金"}

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("成交日期", "发生日期", "交收日期", "交易日期", "日期"),
    "time": ("成交时间", "时间"),
    "code": ("证券代码", "股票代码", "代码"),
    "name": ("证券名称", "股票名称", "名称"),
    "side": ("买卖标志", "买卖方向", "操作", "业务名称", "摘要", "委托类别", "交易类别", "方向"),
    "price": ("成交价格", "成交均价", "成交价", "价格"),
    "quantity": ("成交数量", "成交股数", "发生数量", "数量"),
    "trade_id": ("成交编号", "合同编号", "委托编号"),
    "amount": ("发生金额", "成交金额", "清算金额", "金额"),
}
ACTION_LABELS = {"dividend": "现金分红", "bonus": "送转股", "tax": "红利税补缴"}
# 非买卖行按业务名称识别公司行为；先判断红利税（含「红利」二字），再判断红股、分红
_TAX_WORDS = ("股息红利税补缴", "红利税", "扣税")
_BONUS_WORDS = ("红股入账", "送股", "转增")
_DIVIDEND_WORDS = ("红利入账", "股息入账", "红利发放", "派息")
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


def _action_of(value: Any) -> str:
    text = str(value or "")
    for action, words in (("tax", _TAX_WORDS), ("bonus", _BONUS_WORDS), ("dividend", _DIVIDEND_WORDS)):
        if any(w in text for w in words):
            return action
    return ""


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


def parse_import_rows(rows: list[list[str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, str]:
    """(成交列表, 公司行为列表, 跳过的行数, 错误说明)。按表头识别列，表头前的行（账户信息等）忽略。

    非买卖行按「业务名称/摘要」识别分红送转（红利入账→dividend、红股入账/送股/转增→bonus、红利税→tax），其余跳过。"""
    for i, header in enumerate(rows):
        cells = [c.replace("\ufeff", "").strip() for c in header]
        index = {key: next((cells.index(a) for a in aliases if a in cells), None) for key, aliases in COLUMN_ALIASES.items()}
        if index["code"] is None or index["side"] is None or index["price"] is None or index["quantity"] is None:
            continue
        if index["date"] is None:
            return [], [], 0, "表格里没有成交日期列"
        fee_idx = [cells.index(c) for c in FEE_COLUMNS if c in cells]
        amount_idx = [cells.index(a) for a in COLUMN_ALIASES["amount"] if a in cells]
        trades, actions, skipped, seen, seen_actions = [], [], 0, defaultdict(int), defaultdict(int)
        for row in rows[i + 1:]:
            get = lambda key: row[index[key]] if index[key] is not None and index[key] < len(row) else ""  # noqa: E731
            code_match = re.search(r"\d{6}", get("code"))
            side, date = _side(get("side")), _date(get("date"))
            price, qty = _number(get("price")), _number(get("quantity"))
            if code_match and date and not side:
                action = _action_of(get("side"))
                if action:
                    amount = next((abs(v) for v in (_number(row[j]) for j in amount_idx if j < len(row)) if v), 0.0)
                    shares = int(abs(qty or 0)) if action == "bonus" else 0
                    if (action == "bonus" and shares > 0) or (action != "bonus" and amount > 0):
                        item = {"ex_date": date, "code": code_match.group(), "name": get("name"), "action": action,
                                "cash": 0.0 if action == "bonus" else round(amount, 2), "shares": shares}
                        base = f"CA|{date}|{item['code']}|{action}|{item['cash'] or shares}"
                        seen_actions[base] += 1
                        item["import_key"] = f"{base}#{seen_actions[base]}"[:120]
                        actions.append(item)
                        continue
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
        return trades, actions, skipped, ""
    return [], [], 0, "没有找到表头（需要证券代码、买卖标志、成交价格、成交数量几列）"


def parse_trade_rows(rows: list[list[str]]) -> tuple[list[dict[str, Any]], int, str]:
    """(成交列表, 跳过的行数, 错误说明)，向后兼容的三元组；分红送转行也计入跳过，需要公司行为用 parse_import_rows。"""
    trades, actions, skipped, error = parse_import_rows(rows)
    return trades, skipped + len(actions), error


def _norm_account(name: str | None) -> str:
    """账户名称归一：空 → 默认账户。"""
    return (name or "").strip() or DEFAULT_ACCOUNT


def _account_filter(model, name: str):
    """某个账户的查询条件；默认账户包含 account 为空的旧数据。"""
    if name == DEFAULT_ACCOUNT:
        return or_(model.account.is_(None), model.account == DEFAULT_ACCOUNT)
    return model.account == name


class RealPortfolioService:
    def __init__(self, config: dict | None = None, account: str | None = None):
        """account=None 表示全部账户（只读方法汇总，写方法写入默认账户）；指定账户时只读写该账户。"""
        self.config = config or load_config()
        self.account = _norm_account(account) if (account or "").strip() else None
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        risk = self.config.get("risk") or {}
        self.stop_loss_pct = float(risk.get("stop_loss_pct", -0.05))
        self.take_profit_pct = float(risk.get("take_profit_pct", 0.15))

    @property
    def _write_account(self) -> str:
        return self.account or DEFAULT_ACCOUNT

    def _key(self, import_key: str) -> str:
        """导入去重键：默认账户保持原格式，其他账户前面加「账户|」。"""
        acct = self._write_account
        return import_key if acct == DEFAULT_ACCOUNT else f"{acct}|{import_key}"[:120]

    def _names(self) -> list[str]:
        """当前范围内的账户名称：指定账户时只有它；否则默认账户（始终第一位）+ 账户表 + 数据里出现过的账户。"""
        if self.account:
            return [self.account]
        names = [DEFAULT_ACCOUNT]
        with get_db_session(self.db_path) as session:
            found = [n for (n,) in session.query(RealAccount.name).order_by(RealAccount.id).all()]
            for model in ACCOUNT_TABLES:
                found += [n for (n,) in session.query(model.account).filter(model.account.isnot(None)).distinct().all()]
        for n in found:
            if n and n not in names:
                names.append(n)
        return names

    def _scoped(self, query, model):
        """按当前账户范围过滤（全部账户时不过滤）。"""
        return query.filter(_account_filter(model, self.account)) if self.account else query

    # ---- 流水 ----

    def add_trade(self, trade_date: str, code: str, side: str, price: float, quantity: int, fee: float = 0.0,
                  name: str = "", trade_time: str = "", note: str = "") -> dict[str, Any]:
        side = {"买入": "buy", "卖出": "sell"}.get(side, side)
        if side not in ("buy", "sell") or price <= 0 or quantity <= 0 or not _date(trade_date):
            return {"ok": False, "error": "请填写正确的日期、方向、价格和数量"}
        resolved = self._resolve_code(code, name)
        if isinstance(resolved, str):
            return {"ok": False, "error": resolved}
        bare, name = resolved
        if side == "sell":
            holdings, _, _ = self._replay(account=self._write_account)
            held = holdings.get(bare, {}).get("quantity", 0)
            if quantity > held:
                return {"ok": False, "error": f"卖出 {quantity} 股超过当前持仓 {held} 股"}
        with get_db_session(self.db_path) as session:
            session.add(RealTrade(trade_date=_date(trade_date), trade_time=_time(trade_time), code=bare, name=name or self._name(bare),
                                  side=side, price=price, quantity=int(quantity), fee=fee or 0.0, note=note[:200], source="manual",
                                  account=self._write_account))
        return {"ok": True}

    def _resolve_code(self, code: str, name: str = "") -> tuple[str, str] | str:
        """解析代码/名称/拼音为 (6 位代码, 名称)；找不到时返回中文错误说明。"""
        bare = bare_code(code)
        if len(bare) == 6 and bare.isdigit():
            return bare, name
        from src.services.watchlist import WatchlistService

        resolved = WatchlistService(self.config).resolve(code, include_funds=False)
        if not resolved:
            return f"找不到股票「{code}」"
        return resolved[0], name or resolved[1]

    def delete_trade(self, trade_id: int) -> bool:
        with get_db_session(self.db_path) as session:
            return session.query(RealTrade).filter(RealTrade.id == trade_id).delete() > 0

    def _existing_keys(self) -> tuple[set[str], set[str]]:
        with get_db_session(self.db_path) as session:
            trade_keys = {k for (k,) in session.query(RealTrade.import_key).filter(RealTrade.import_key.isnot(None)).all()}
            action_keys = {k for (k,) in session.query(RealCorporateAction.import_key).filter(RealCorporateAction.import_key.isnot(None)).all()}
        return trade_keys, action_keys

    def _keyed(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按目标账户给导入记录的去重键加前缀（默认账户不变）。"""
        for item in items:
            item["import_key"] = self._key(item["import_key"])
        return items

    def preview_import(self, path: str) -> dict[str, Any]:
        """只解析交割单、不写库：新增/重复/跳过数量和前 200 条成交。"""
        trades, actions, skipped, error = parse_import_rows(read_rows(path))
        self._keyed(trades), self._keyed(actions)
        fmt = "excel" if Path(path).suffix.lower() in (".xlsx", ".xls") else "csv"
        result: dict[str, Any] = {"format": fmt, "trades": [], "actions": [], "new_trades": 0, "new_actions": 0, "duplicates": 0,
                                  "skipped": skipped, "warnings": [], "error": error}
        if error:
            return result
        trade_keys, action_keys = self._existing_keys()
        for t in trades:
            t["duplicate"] = t["import_key"] in trade_keys
        for a in actions:
            a["duplicate"] = a["import_key"] in action_keys
            a["action_label"] = ACTION_LABELS[a["action"]]
        result.update(
            trades=trades[:200], actions=actions,
            new_trades=sum(not t["duplicate"] for t in trades), new_actions=sum(not a["duplicate"] for a in actions),
            duplicates=sum(t["duplicate"] for t in trades) + sum(a["duplicate"] for a in actions),
        )
        if not trades and not actions:
            result["warnings"].append("没有识别到任何成交或分红送转记录")
        if len(trades) > 200:
            result["warnings"].append(f"共 {len(trades)} 笔成交，仅显示前 200 笔")
        return result

    def import_file(self, path: str) -> dict[str, Any]:
        trades, actions, skipped, error = parse_import_rows(read_rows(path))
        if error:
            return {"added": 0, "duplicate": 0, "skipped": skipped, "actions_added": 0, "error": error}
        self._keyed(trades), self._keyed(actions)
        account = self._write_account
        added = duplicate = actions_added = 0
        with get_db_session(self.db_path) as session:
            existing = {k for (k,) in session.query(RealTrade.import_key).filter(RealTrade.import_key.isnot(None)).all()}
            existing_actions = {k for (k,) in session.query(RealCorporateAction.import_key).filter(RealCorporateAction.import_key.isnot(None)).all()}
            for t in trades:
                if t["import_key"] in existing:
                    duplicate += 1
                    continue
                session.add(RealTrade(**t, source="import", account=account))
                existing.add(t["import_key"])
                added += 1
            for a in actions:
                if a["import_key"] in existing_actions:
                    duplicate += 1
                    continue
                session.add(RealCorporateAction(**a, source="import", account=account))
                existing_actions.add(a["import_key"])
                actions_added += 1
        logger.info(f"导入实盘流水（{account}）：新增 {added}，分红送转 {actions_added}，重复 {duplicate}，跳过 {skipped}")
        return {"added": added, "duplicate": duplicate, "skipped": skipped, "actions_added": actions_added, "error": ""}

    def trades(self, limit: int = 500) -> list[dict[str, Any]]:
        with get_db_session(self.db_path) as session:
            rows = (self._scoped(session.query(RealTrade), RealTrade)
                    .order_by(RealTrade.trade_date.desc(), RealTrade.trade_time.desc(), RealTrade.id.desc())
                    .limit(limit).all())
            return [{"id": r.id, "trade_date": r.trade_date, "trade_time": r.trade_time or "", "code": r.code, "name": r.name or "",
                     "side": r.side, "price": r.price, "quantity": r.quantity, "fee": r.fee or 0.0, "source": r.source,
                     "note": r.note or "", "account": r.account or DEFAULT_ACCOUNT} for r in rows]

    # ---- 分红送转 ----

    def add_corporate_action(self, ex_date: str, code: str, action: str, cash: float = 0.0, shares: int = 0,
                             note: str = "", name: str = "") -> dict[str, Any]:
        """记一笔分红送转：dividend/tax 填金额（元，正数），bonus 填到账股数。"""
        action = {"现金分红": "dividend", "送转股": "bonus", "红利税补缴": "tax"}.get(action, action)
        if action not in ACTION_LABELS:
            return {"ok": False, "error": "类型只能是现金分红、送转股或红利税补缴"}
        if not _date(ex_date):
            return {"ok": False, "error": "请填写正确的除权日期"}
        cash, shares = float(cash or 0), int(shares or 0)
        if action != "bonus" and cash <= 0:
            return {"ok": False, "error": "金额必须大于 0"}
        if action == "bonus" and shares <= 0:
            return {"ok": False, "error": "送转股数必须大于 0"}
        resolved = self._resolve_code(code, name)
        if isinstance(resolved, str):
            return {"ok": False, "error": resolved}
        bare, name = resolved
        name = name or self._name(bare)
        item = {"ex_date": _date(ex_date), "code": bare, "name": name, "action": action,
                "cash": 0.0 if action == "bonus" else round(cash, 2), "shares": shares if action == "bonus" else 0, "note": (note or "")[:200]}
        with get_db_session(self.db_path) as session:
            row = RealCorporateAction(**item, source="manual", account=self._write_account)
            session.add(row)
            session.flush()
            item_id = row.id
        return {"ok": True, "id": item_id, **item, "action_label": ACTION_LABELS[action], "source": "manual", "account": self._write_account}

    def add_corporate_action_by_plan(self, ex_date: str, code: str, cash_per_10: float = 0.0, bonus_per_10: float = 0.0,
                                     transfer_per_10: float = 0.0, tax_rate: float = 0.0, note: str = "") -> dict[str, Any]:
        """按分红方案（每 10 股派息/送股/转增）和除权日前一刻的持仓数量，生成现金分红与送转股两笔记录。"""
        if not _date(ex_date):
            return {"ok": False, "error": "请填写正确的除权日期"}
        cash_per_10, bonus_per_10, transfer_per_10, tax_rate = (float(v or 0) for v in (cash_per_10, bonus_per_10, transfer_per_10, tax_rate))
        if min(cash_per_10, bonus_per_10, transfer_per_10, tax_rate) < 0 or tax_rate >= 1:
            return {"ok": False, "error": "分红方案的数值不能为负数，红利税率需小于 1"}
        if cash_per_10 <= 0 and bonus_per_10 + transfer_per_10 <= 0:
            return {"ok": False, "error": "请填写每 10 股的派息、送股或转增数量"}
        resolved = self._resolve_code(code)
        if isinstance(resolved, str):
            return {"ok": False, "error": resolved}
        bare = resolved[0]
        holdings, _, _ = self._replay(before=_date(ex_date), account=self._write_account)
        held = holdings.get(bare, {}).get("quantity", 0)
        if held <= 0:
            return {"ok": False, "error": "除权日前没有持仓"}
        created = []
        cash = round(held * cash_per_10 / 10 * (1 - tax_rate), 2)
        if cash > 0:
            created.append(self.add_corporate_action(ex_date, bare, "dividend", cash=cash, note=note, name=resolved[1]))
        shares = int(held * (bonus_per_10 + transfer_per_10) / 10 + 1e-9)
        if shares > 0:
            created.append(self.add_corporate_action(ex_date, bare, "bonus", shares=shares, note=note, name=resolved[1]))
        if not created:
            return {"ok": False, "error": f"按持仓 {held} 股计算，分红金额和送转股数都不足一份"}
        return {"ok": True, "holding": held, "created": created}

    def delete_corporate_action(self, action_id: int) -> bool:
        with get_db_session(self.db_path) as session:
            return session.query(RealCorporateAction).filter(RealCorporateAction.id == action_id).delete() > 0

    def corporate_actions(self, limit: int = 500) -> list[dict[str, Any]]:
        with get_db_session(self.db_path) as session:
            rows = (self._scoped(session.query(RealCorporateAction), RealCorporateAction)
                    .order_by(RealCorporateAction.ex_date.desc(), RealCorporateAction.id.desc()).limit(limit).all())
            return [{"id": r.id, "code": r.code, "name": r.name or "", "ex_date": r.ex_date, "action": r.action,
                     "action_label": ACTION_LABELS.get(r.action, r.action), "cash": r.cash or 0.0, "shares": r.shares or 0,
                     "note": r.note or "", "source": r.source, "account": r.account or DEFAULT_ACCOUNT} for r in rows]

    def _ordered_actions(self, account: str | None = None) -> list[RealCorporateAction]:
        """按除权日排序的公司行为；account 为具体账户名时只取该账户，None 时取当前范围内全部。"""
        with get_db_session(self.db_path) as session:
            query = session.query(RealCorporateAction)
            query = query.filter(_account_filter(RealCorporateAction, account)) if account else self._scoped(query, RealCorporateAction)
            rows = query.order_by(RealCorporateAction.ex_date, RealCorporateAction.id).all()
            session.expunge_all()
            return rows

    # ---- 资金与计划 ----

    # ---- 出入金 ----

    def add_cash_flow(self, flow_date: str, direction: str, amount: float, note: str = "") -> dict[str, Any]:
        """记一笔入金（in）或出金（out），金额为正数。"""
        flow_date = _date(flow_date)
        if not flow_date:
            return {"ok": False, "error": "日期格式不对，应为 YYYY-MM-DD"}
        if direction not in FLOW_LABELS:
            return {"ok": False, "error": "方向只能是入金或出金"}
        amount = float(amount or 0)
        if amount <= 0:
            return {"ok": False, "error": "金额必须大于 0"}
        with get_db_session(self.db_path) as session:
            row = RealCashFlow(flow_date=flow_date, direction=direction, amount=round(amount, 2), note=(note or "")[:200],
                               account=self._write_account)
            session.add(row)
            session.flush()
            return {"ok": True, "id": row.id}

    def delete_cash_flow(self, flow_id: int) -> bool:
        with get_db_session(self.db_path) as session:
            return self._scoped(session.query(RealCashFlow).filter(RealCashFlow.id == flow_id), RealCashFlow).delete() > 0

    def cash_flows(self, limit: int = 500) -> list[dict[str, Any]]:
        with get_db_session(self.db_path) as session:
            rows = (self._scoped(session.query(RealCashFlow), RealCashFlow)
                    .order_by(RealCashFlow.flow_date.desc(), RealCashFlow.id.desc()).limit(limit).all())
            return [{"id": r.id, "flow_date": r.flow_date, "direction": r.direction, "direction_label": FLOW_LABELS.get(r.direction, r.direction),
                     "amount": r.amount, "note": r.note or "", "account": _norm_account(r.account)} for r in rows]

    def _flows(self, account: str) -> list[RealCashFlow]:
        with get_db_session(self.db_path) as session:
            rows = (session.query(RealCashFlow).filter(_account_filter(RealCashFlow, account))
                    .order_by(RealCashFlow.flow_date, RealCashFlow.id).all())
            session.expunge_all()   # 退出会话提交前先脱离，否则对象过期、之后读属性报 DetachedInstanceError
            return rows

    def set_cash(self, cash: float, as_of: datetime | None = None) -> None:
        with get_db_session(self.db_path) as session:
            session.add(RealCash(cash=float(cash), as_of=as_of or datetime.now(), account=self._write_account))

    def set_plan(self, code: str, stop_loss: float | None, target_price: float | None) -> None:
        bare = bare_code(code)
        with get_db_session(self.db_path) as session:
            plan = (session.query(RealPositionPlan).filter(RealPositionPlan.code == bare, _account_filter(RealPositionPlan, self._write_account))
                    .first() or RealPositionPlan(code=bare))
            plan.account = self._write_account
            plan.stop_loss, plan.target_price = stop_loss or None, target_price or None
            session.add(plan)

    # ---- 持仓 ----

    def _ordered_trades(self, account: str | None = None) -> list[RealTrade]:
        with get_db_session(self.db_path) as session:
            query = session.query(RealTrade)
            query = query.filter(_account_filter(RealTrade, account)) if account else self._scoped(query, RealTrade)
            rows = query.order_by(RealTrade.trade_date, RealTrade.trade_time, RealTrade.id).all()
            session.expunge_all()
            return rows

    def _replay(self, before: str | None = None, account: str | None = None) -> tuple[dict[str, dict[str, Any]], float, list[str]]:
        """按时间顺序重放某个账户（默认账户）的成交和分红送转：持仓（数量、平均成本、名称）、已实现盈亏、提示。

        同一天先处理公司行为（除权除息在开盘前生效）再处理当天成交；before 给出时只重放该日期之前的记录。
        现金分红摊薄持仓总成本（可为负，不计入已实现盈亏），送转股只增加数量，红利税补缴计入成本。"""
        account = _norm_account(account)
        holdings: dict[str, dict[str, Any]] = {}
        realized, warnings = 0.0, []
        events = [(a.ex_date, 0, "", a.id, a) for a in self._ordered_actions(account)]
        events += [(t.trade_date, 1, t.trade_time or "", t.id, t) for t in self._ordered_trades(account)]
        for day, kind, _, _, item in sorted(events, key=lambda e: e[:4]):
            if before and day >= before:
                continue
            if kind == 0:
                pos = holdings.get(item.code)
                if pos is None or pos["quantity"] <= 0:
                    warnings.append(f"{item.ex_date} {item.name or item.code} 分红送转时没有持仓，已忽略")
                    continue
                pos["name"] = item.name or pos["name"]
                if item.action == "dividend":
                    pos["cost"] -= item.cash or 0.0
                elif item.action == "bonus":
                    pos["quantity"] += item.shares or 0
                elif item.action == "tax":
                    pos["cost"] += item.cash or 0.0
                continue
            t = item
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
        names = self._names()
        merged: dict[str, dict[str, Any]] = {}
        for account in names:
            holdings, _, _ = self._replay(account=account)
            plans = self._plans(account)
            for code, pos in holdings.items():
                plan = plans.get(code, {})
                m = merged.get(code)
                if m is None:
                    merged[code] = {"quantity": pos["quantity"], "cost": pos["cost"], "name": pos["name"], "first_date": pos["first_date"],
                                    "stop_loss": plan.get("stop_loss"), "target_price": plan.get("target_price"), "accounts": [account]}
                    continue
                m["quantity"] += pos["quantity"]
                m["cost"] += pos["cost"]
                m["name"] = m["name"] or pos["name"]
                m["first_date"] = min(m["first_date"], pos["first_date"])
                m["stop_loss"] = m["stop_loss"] or plan.get("stop_loss")
                m["target_price"] = m["target_price"] or plan.get("target_price")
                m["accounts"].append(account)
        result = []
        with get_db_session(self.db_path) as session:
            for code, pos in merged.items():
                avg = pos["cost"] / pos["quantity"]
                price = (session.query(StockDaily.close).filter(StockDaily.code.in_(code_candidates(code)), StockDaily.close > 0)
                         .order_by(StockDaily.trade_date.desc()).limit(1).scalar()) or avg
                result.append({
                    "account": "real", "code": code, "name": pos["name"] or self._name(code), "quantity": pos["quantity"],
                    "available_quantity": pos["quantity"], "avg_cost": round(avg, 4), "market_price": price,
                    "market_value": price * pos["quantity"], "unrealized_pnl": (price - avg) * pos["quantity"],
                    "stop_loss": pos["stop_loss"] or round(avg * (1 + self.stop_loss_pct), 2),
                    "target_price": pos["target_price"] or round(avg * (1 + self.take_profit_pct), 2),
                    "first_date": pos["first_date"], "accounts": pos["accounts"],
                })
        return sorted(result, key=lambda p: -p["market_value"])

    def snapshot(self) -> dict[str, Any]:
        """与 ExecutionService.get_trading_snapshot() 同样的结构，供组合风险等复用。"""
        positions = self.positions()
        names = self._names()
        realized, warnings = 0.0, []
        for account in names:
            _, r, w = self._replay(account=account)
            realized += r
            warnings += w if len(names) == 1 else [f"[{account}] {x}" for x in w]
        realized = round(realized, 2)
        market_value = sum(p["market_value"] for p in positions)
        cash = self.cash()
        cash_known = cash is not None and not self._missing_cash_accounts(names)
        if not cash_known:
            warnings.append("还没有设置可用资金，也没有记出入金，总资产只按持仓市值计算")
        flows = [f for a in names for f in self._flows(a)]
        net_deposit = round(sum(f.amount if f.direction == "in" else -f.amount for f in flows), 2)
        # 出入金模式：都没有设置可用资金、全靠出入金重放时，累计收益 = 总资产 − 净入金
        ledger_mode = bool(flows) and cash_known and not any(self._has_anchor(a) for a in names)
        total_assets = (cash or 0.0) + market_value
        return {
            "account": {"broker": "real", "cash": cash or 0.0, "market_value": market_value, "total_assets": total_assets,
                        "unrealized_pnl": sum(p["unrealized_pnl"] for p in positions), "realized_pnl": realized,
                        "cash_known": cash_known, "net_deposit": net_deposit, "ledger_mode": ledger_mode,
                        "total_return": round(total_assets - net_deposit, 2) if ledger_mode else None},
            "positions": positions, "warnings": warnings,
        }

    def _missing_cash_accounts(self, names: list[str]) -> list[str]:
        """有成交但既没有设置可用资金、也没有记出入金的账户（只在多账户汇总时有意义，单账户语义由 cash() 是否为空决定）。"""
        if len(names) <= 1:
            return []
        missing = []
        with get_db_session(self.db_path) as session:
            for account in names:
                has_trade = session.query(RealTrade.id).filter(_account_filter(RealTrade, account)).first() is not None
                has_cash = any(session.query(m.id).filter(_account_filter(m, account)).first() is not None for m in (RealCash, RealCashFlow))
                if has_trade and not has_cash:
                    missing.append(account)
        return missing

    def _has_anchor(self, account: str) -> bool:
        with get_db_session(self.db_path) as session:
            return session.query(RealCash.id).filter(_account_filter(RealCash, account)).first() is not None

    def _account_cash(self, account: str) -> float | None:
        """某个账户的可用资金：
        - 设置过可用资金：最近一次设置的金额 + 之后的出入金、成交和分红带来的变化（出入金按当天结束时计，
          设置当天及之后日期的出入金都算在设置之后）
        - 没设置但记了出入金：从零开始按全部出入金、成交和分红重放
        - 都没有：None"""
        with get_db_session(self.db_path) as session:
            anchor = (session.query(RealCash).filter(_account_filter(RealCash, account))
                      .order_by(RealCash.as_of.desc(), RealCash.id.desc()).first())
            anchor_value = (anchor.cash, anchor.as_of) if anchor is not None else None
        flows = self._flows(account)
        if anchor_value is None and not flows:
            return None
        cash, as_of = anchor_value if anchor_value else (0.0, datetime.min)
        for f in flows:
            if _when(f.flow_date, "23:59:59") > as_of:
                cash += f.amount if f.direction == "in" else -f.amount
        for t in self._ordered_trades(account):
            if _when(t.trade_date, t.trade_time) > as_of:
                amount = t.price * t.quantity
                cash += -(amount + (t.fee or 0)) if t.side == "buy" else amount - (t.fee or 0)
        for a in self._ordered_actions(account):
            if a.action != "bonus" and _when(a.ex_date, "09:00:00") > as_of:
                cash += (a.cash or 0.0) if a.action == "dividend" else -(a.cash or 0.0)
        return round(cash, 2)

    def cash(self) -> float | None:
        """当前可用资金；全部账户时为各账户之和（只算设置过的账户），都没设置时为 None。"""
        values = [v for v in (self._account_cash(a) for a in self._names()) if v is not None]
        return round(sum(values), 2) if values else None

    def fills(self) -> list[tuple[str, str, float, int, datetime]]:
        """(code, side, 含手续费的成交价, 数量, 成交时间)，供组合风险按净值重放计算回撤。"""
        result = []
        for t in self._ordered_trades():
            fee_per_share = (t.fee or 0) / t.quantity if t.quantity else 0
            price = t.price + fee_per_share if t.side == "buy" else t.price - fee_per_share
            result.append((t.code, t.side, price, t.quantity, _when(t.trade_date, t.trade_time)))
        # 送转股按 0 成本买入，使净值重放在除权日保持连续；现金分红/红利税不进 fills（近似：
        # 组合风险的期初资金按「现金 + 成交流水」倒推，分红金额已在现金里，回撤序列只是略微低估除息日后的净值）
        result += [(a.code, "buy", 0.0, a.shares or 0, _when(a.ex_date, "09:00:00")) for a in self._ordered_actions() if a.action == "bonus"]
        result.sort(key=lambda f: f[4])
        return result

    def _plans(self, account: str) -> dict[str, dict[str, float | None]]:
        with get_db_session(self.db_path) as session:
            rows = session.query(RealPositionPlan).filter(_account_filter(RealPositionPlan, account)).all()
            return {p.code: {"stop_loss": p.stop_loss, "target_price": p.target_price} for p in rows}

    def _name(self, code: str) -> str:
        with get_db_session(self.db_path) as session:
            return (session.query(StockDaily.name).filter(StockDaily.code.in_(code_candidates(code)), StockDaily.name.isnot(None))
                    .order_by(StockDaily.trade_date.desc()).limit(1).scalar()) or ""

    # ---- 账户管理 ----

    def accounts(self) -> list[dict[str, Any]]:
        """全部账户（默认账户始终第一位）：成交笔数、持仓只数、市值、现金（没设置为 None）。"""
        base = RealPortfolioService(self.config)
        with get_db_session(self.db_path) as session:
            info = {r.name: (r.broker or "", r.note or "") for r in session.query(RealAccount).all()}
        result = []
        for name in base._names():
            sub = RealPortfolioService(self.config, account=name)
            positions = sub.positions()
            with get_db_session(self.db_path) as session:
                trades = session.query(RealTrade.id).filter(_account_filter(RealTrade, name)).count()
            broker, note = info.get(name, ("", ""))
            result.append({"name": name, "broker": broker, "note": note, "trades": trades, "positions": len(positions),
                           "market_value": round(sum(p["market_value"] for p in positions), 2), "cash": sub.cash()})
        return result

    @staticmethod
    def _check_account_name(name: str) -> str:
        """校验账户名称，返回错误说明（合法为空字符串）。"""
        if not name or len(name) > 30:
            return "账户名称需为 1~30 个字"
        if name == ALL_ACCOUNTS_LABEL:
            return f"账户名称不能叫「{ALL_ACCOUNTS_LABEL}」"
        return ""

    def add_account(self, name: str, broker: str = "", note: str = "") -> dict[str, Any]:
        name = (name or "").strip()
        error = self._check_account_name(name)
        if error:
            return {"ok": False, "error": error}
        if name in RealPortfolioService(self.config)._names():
            return {"ok": False, "error": f"账户「{name}」已存在"}
        with get_db_session(self.db_path) as session:
            session.add(RealAccount(name=name, broker=(broker or "").strip()[:30], note=(note or "").strip()[:200]))
        return {"ok": True, "name": name}

    def rename_account(self, old: str, new: str, broker: str | None = None, note: str | None = None) -> dict[str, Any]:
        """改名（同步四张表的 account 列），并可同时更新券商和备注；默认账户不能改名。"""
        old, new = (old or "").strip(), (new or "").strip()
        if old == DEFAULT_ACCOUNT:
            return {"ok": False, "error": "默认账户不能改名"}
        names = RealPortfolioService(self.config)._names()
        if old not in names:
            return {"ok": False, "error": f"账户「{old}」不存在"}
        error = self._check_account_name(new)
        if error:
            return {"ok": False, "error": error}
        if new != old and new in names:
            return {"ok": False, "error": f"账户「{new}」已存在"}
        with get_db_session(self.db_path) as session:
            row = session.query(RealAccount).filter(RealAccount.name == old).first()
            if row is None:
                row = RealAccount(name=old)
                session.add(row)
            row.name = new
            if broker is not None:
                row.broker = broker.strip()[:30]
            if note is not None:
                row.note = note.strip()[:200]
            if new != old:
                for model in ACCOUNT_TABLES:
                    session.query(model).filter(model.account == old).update({"account": new}, synchronize_session=False)
        return {"ok": True, "name": new}

    def delete_account(self, name: str) -> dict[str, Any]:
        name = (name or "").strip()
        if name == DEFAULT_ACCOUNT:
            return {"ok": False, "error": "默认账户不能删除"}
        if name not in RealPortfolioService(self.config)._names():
            return {"ok": False, "error": f"账户「{name}」不存在"}
        with get_db_session(self.db_path) as session:
            trades = session.query(RealTrade.id).filter(RealTrade.account == name).count()
            actions = session.query(RealCorporateAction.id).filter(RealCorporateAction.account == name).count()
            flows = session.query(RealCashFlow.id).filter(RealCashFlow.account == name).count()
            if trades or actions or flows:
                return {"ok": False, "error": f"账户「{name}」还有 {trades} 笔成交、{actions} 条分红送转、{flows} 笔出入金记录，请先删除或改到其他账户"}
            for model in (RealCash, RealPositionPlan, RealAccount):
                col = model.name if model is RealAccount else model.account
                session.query(model).filter(col == name).delete(synchronize_session=False)
        return {"ok": True}
