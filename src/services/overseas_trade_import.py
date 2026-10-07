"""海外券商成交 CSV：先完整校验、去重、重放，再原子保存。"""
import csv
from datetime import date, datetime
import hashlib
import io
import json
import math

from src.database.db import get_db_session
from src.database.models import OverseasTrade, OverseasHolding
from src.utils.stock_code import resolve_identity

SIDE = ("side", "action", "buy/sell", "方向", "买卖方向", "交易方向", "交易类型", "操作", "买卖")
FIELDS = {
    "trade_date": ("trade_date", "date", "date/time", "成交日期", "成交时间", "发生日期", "日期"),
    "code": ("symbol", "ticker", "code", "证券代码", "股票代码", "代码"),
    "quantity": ("quantity", "qty", "成交数量", "数量"),
    "price": ("price", "tradeprice", "t. price", "成交价格", "成交均价", "成交价", "价格"),
    "fee": ("fee", "commission", "佣金", "手续费", "费用"),
    "currency": ("currency", "币种", "货币"),
    "market": ("market", "市场", "交易市场"),
    "trade_uid": ("trade_id", "tradeid", "trade_uid", "成交编号", "成交序号", "流水号", "合同编号", "委托编号"),
    "side": SIDE,
}
KEYS = ("import_key", "code", "currency", "trade_date", "side", "quantity", "price", "fee")


def reader(text):
    return csv.DictReader(io.StringIO(text.lstrip("\ufeff")))


def is_trade_csv(text: str) -> bool:
    return bool(set(str(k).lower().strip() for k in (reader(text).fieldnames or ())) & set(SIDE))


def parse(text: str, as_of: str) -> tuple[list[dict], list[dict]]:
    rows, errors = [], []
    for line, raw in enumerate(reader(text), 2):
        if line > 5001:
            raise ValueError("单次最多导入 5000 行")
        record = {str(k).lower().strip(): str(v or "").strip() for k, v in raw.items()}
        values = {key: next((record[k] for k in aliases if record.get(k)), "") for key, aliases in FIELDS.items()}
        try:
            day = values["trade_date"].replace("/", "-")[:10]
            if len(day) == 8 and day.isdigit():
                day = day[:4] + "-" + day[4:6] + "-" + day[6:]
            day = date.fromisoformat(day).isoformat()
            if day > as_of:
                raise ValueError("成交日期晚于导入截止日期")
            market = {"港股": "HK", "美股": "US", "日股": "JP", "韩股": "KR", "台股": "TW"}.get(values["market"], values["market"].upper())
            if not market:
                market = {"HKD": "HK", "JPY": "JP", "KRW": "KR", "TWD": "TW"}.get(values["currency"].upper(), "")
            code = values["code"]
            identity = resolve_identity(market + ":" + code if market and code.isdigit() else code)
            if identity.market == "CN":
                raise ValueError("A 股成交请使用实盘记账导入")
            currency = values["currency"].upper() or identity.currency
            if currency != identity.currency:
                raise ValueError("币种与证券不符")
            side = {"buy": "buy", "b": "buy", "买入": "buy", "证券买入": "buy", "买": "buy",
                    "sell": "sell", "s": "sell", "卖出": "sell", "证券卖出": "sell", "卖": "sell"}.get(values["side"].lower())
            if not side:
                raise ValueError("无法识别买卖方向")
            quantity, price, fee = [float(values[key].replace(",", "") or 0) for key in ("quantity", "price", "fee")]
            if not all(math.isfinite(v) for v in (quantity, price, fee)) or quantity <= 0 or price <= 0 or fee < 0:
                raise ValueError("数量、价格或费用无效")
            row = {"code": identity.code, "currency": currency, "trade_date": day, "side": side,
                   "quantity": quantity, "price": price, "fee": fee}
            key = "uid:" + values["trade_uid"] if values["trade_uid"] else json.dumps(row, sort_keys=True)
            row["import_key"] = hashlib.sha256(key.encode()).hexdigest()
            rows.append(row)
        except ValueError as error:
            errors.append({"line": line, "error": str(error)})
    return rows, errors


def replay(rows: list[dict]) -> list[dict]:
    holdings = {}
    for row in sorted(rows, key=lambda r: r["trade_date"]):
        holding = holdings.setdefault(row["code"], {"code": row["code"], "currency": row["currency"], "quantity": 0., "cost": 0., "as_of": row["trade_date"]})
        if row["side"] == "buy":
            holding["quantity"] += row["quantity"]
            holding["cost"] += row["quantity"] * row["price"] + row["fee"]
        else:
            if row["quantity"] > holding["quantity"] + 1e-8:
                raise ValueError(f"{row['code']} 卖出超过已导入数量，请补齐更早买入记录")
            average = holding["cost"] / holding["quantity"]
            holding["quantity"] = max(0., holding["quantity"] - row["quantity"])
            holding["cost"] = average * holding["quantity"]
        holding["as_of"] = row["trade_date"]
    return [{"code": h["code"], "currency": h["currency"], "quantity": h["quantity"], "as_of": h["as_of"],
             "average_cost": h["cost"] / h["quantity"] if h["quantity"] else None} for h in holdings.values()]


def import_trades(db_path: str, text: str, account: str, as_of: str, *, preview: bool) -> dict:
    if not account.strip() or len(account) > 100 or date.fromisoformat(as_of) > date.today():
        raise ValueError("账户名称或截止日期无效")
    account = account.strip()
    incoming, errors = parse(text, as_of)
    if errors or not incoming:
        return {"ok": False, "saved": 0, "rows": [], "errors": errors or [{"line": 1, "error": "没有有效成交"}]}
    with get_db_session(db_path) as session:
        if session.query(OverseasHolding).filter(OverseasHolding.account == account, OverseasHolding.source != "trade_csv").first():
            raise ValueError("该账户已有持仓快照，请使用另一个账户名称导入完整成交账本")
        existing = [{key: getattr(row, key) for key in KEYS} for row in session.query(OverseasTrade).filter_by(account=account).order_by(OverseasTrade.id)]
        by_key = {row["import_key"]: row for row in existing}
        added, skipped = [], 0
        for row in incoming:
            previous = by_key.get(row["import_key"])
            if previous:
                if previous != row:
                    raise ValueError("相同成交编号对应不同内容，导入已停止")
                skipped += 1
            else:
                by_key[row["import_key"]] = row
                added.append(row)
        positions = replay(list(by_key.values()))
        if not preview:
            session.add_all([OverseasTrade(account=account, **row) for row in added])
            for value in positions:
                holding = session.query(OverseasHolding).filter_by(account=account, code=value["code"]).first()
                if holding is None:
                    holding = OverseasHolding(account=account, source="trade_csv")
                    session.add(holding)
                for key, val in value.items():
                    setattr(holding, key, val)
                holding.updated_at = datetime.now()
        return {"ok": True, "saved": len(added) if not preview else 0, "rows": positions,
                "errors": [], "new_trades": len(added), "duplicates": skipped, "mode": "trades"}
