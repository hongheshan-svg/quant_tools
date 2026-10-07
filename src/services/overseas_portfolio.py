"""海外持仓导入和原币/人民币估值；缺价、缺汇率不回退成本价。"""
from __future__ import annotations

import csv
from datetime import date, datetime
import io
import math

from src.database.db import get_db_session
from src.database.models import OverseasHolding
from src.utils.stock_code import resolve_identity
from src.services.overseas_research import OverseasResearchService

ALIASES = {
    "code": ("symbol", "ticker", "code", "证券代码", "股票代码", "代码"),
    "quantity": ("quantity", "qty", "position", "持仓数量", "持有数量", "持仓", "数量"),
    "average_cost": ("average_cost", "avg cost", "cost_price", "cost basis price", "成本价", "摊薄成本价", "平均成本价"),
    "currency": ("currency", "币种", "货币"),
    "market": ("market", "市场", "交易市场"),
}


def parse_csv(text: str, account: str, as_of: str, source: str = "csv") -> dict:
    if not account.strip() or len(account) > 100:
        raise ValueError("请填写账户名称（最多 100 字）")
    day = date.fromisoformat(as_of)
    if day > date.today():
        raise ValueError("持仓快照日期不能在未来")
    rows, errors, seen = [], [], set()
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    for line, original in enumerate(reader, 2):
        if line > 5001:
            raise ValueError("单次最多导入 5000 行")
        record = {str(k).strip().lower(): str(v or "").strip() for k, v in original.items()}
        values = {key: next((record[k] for k in aliases if record.get(k)), "") for key, aliases in ALIASES.items()}
        try:
            code = values["code"]
            market = values["market"].upper()
            if not market and code.isdigit():
                market = {"HKD": "HK", "JPY": "JP", "KRW": "KR", "TWD": "TW"}.get(values["currency"].upper(), "")
            market = {"港股": "HK", "美股": "US", "日股": "JP", "韩股": "KR", "台股": "TW"}.get(market, market)
            if market in ("HK", "US", "JP", "KR", "TW") and (code.isdigit() or market == "US"):
                code = market + ":" + code
            identity = resolve_identity(code)
            if identity.market == "CN":
                raise ValueError("A 股请使用实盘记账导入")
            currency = values["currency"].upper() or identity.currency
            if currency != identity.currency:
                raise ValueError("币种与证券市场不符")
            quantity = float(values["quantity"].replace(",", ""))
            cost = float(values["average_cost"].replace(",", "")) if values["average_cost"] else None
            if not math.isfinite(quantity) or quantity < 0 or (cost is not None and (not math.isfinite(cost) or cost <= 0)):
                raise ValueError("数量或成本无效；不支持空头持仓")
            if identity.code in seen:
                raise ValueError("同一快照存在重复证券，请先合并数量和加权成本")
            seen.add(identity.code)
            rows.append({"account": account.strip(), "code": identity.code, "currency": currency, "quantity": quantity,
                         "average_cost": cost, "as_of": as_of, "source": source})
        except (ValueError, TypeError) as error:
            errors.append({"line": line, "error": str(error)})
    if not rows and not errors:
        errors.append({"line": 1, "error": "没有可导入的持仓，请检查表头"})
    return {"rows": rows, "errors": errors, "ok": bool(rows) and not errors}


class OverseasPortfolioService:
    def __init__(self, config: dict):
        self.config = config
        self.research = OverseasResearchService(config)
        self.db_path = self.research.db_path

    def import_csv(self, text: str, account: str, as_of: str, *, preview: bool = True) -> dict:
        from src.services.overseas_trade_import import is_trade_csv, import_trades
        if is_trade_csv(text):
            return import_trades(self.db_path, text, account, as_of, preview=preview)
        parsed = parse_csv(text, account, as_of)
        if preview or not parsed["ok"]:
            return {**parsed, "saved": 0}
        with get_db_session(self.db_path) as session:
            from src.database.models import OverseasTrade
            if session.query(OverseasTrade).filter_by(account=account.strip()).first():
                raise ValueError("该账户已有成交账本，请使用另一个账户名称导入持仓快照")
            for values in parsed["rows"]:
                row = session.query(OverseasHolding).filter_by(account=values["account"], code=values["code"]).first()
                if row and row.as_of > values["as_of"]:
                    raise ValueError("导入快照早于已保存持仓，请检查日期")
            for values in parsed["rows"]:
                row = session.query(OverseasHolding).filter_by(account=values["account"], code=values["code"]).first()
                if row is None:
                    row = OverseasHolding()
                    session.add(row)
                for key, value in values.items():
                    setattr(row, key, value)
                row.updated_at = datetime.now()
        return {**parsed, "saved": len(parsed["rows"])}

    def portfolio(self, *, refresh: bool = False) -> dict:
        with get_db_session(self.db_path) as session:
            holdings = [{key: getattr(row, key) for key in ("account", "code", "currency", "quantity", "average_cost", "as_of", "source")}
                        for row in session.query(OverseasHolding).filter(OverseasHolding.quantity > 0).order_by(OverseasHolding.account, OverseasHolding.code)]
        currencies = {h["currency"] for h in holdings}
        rates = {currency: self.research.fx_rate(currency, refresh=refresh) for currency in currencies}
        rows, total, complete = [], 0.0, True
        snapshots = {}
        for holding in holdings:
            code = holding["code"]
            if code not in snapshots:
                snapshots[code] = self.research.refresh(code) if refresh else self.research.snapshot(code)
            snapshot = snapshots[code]
            price = snapshot["quote"].get("close")
            quote_ok = snapshot["quality"]["status"] == "available"
            fx = rates[holding["currency"]]
            original_value = holding["quantity"] * price if quote_ok and price else None
            converted = original_value * fx["rate"] if original_value is not None and fx["status"] == "available" else None
            if converted is None:
                complete = False
            else:
                total += converted
            rows.append({**holding, "price": price, "quote_date": snapshot["quote"].get("trade_date"),
                "price_quality": snapshot["quality"], "fx": fx, "market_value": original_value,
                "market_value_cny": round(converted, 2) if converted is not None else None})
        return {"positions": rows, "base_currency": "CNY", "market_value_cny": round(total, 2) if complete else None,
                "status": "available" if complete else "partial", "limitations": [] if complete else ["missing_quote_or_fx"],
                "cash_included": False, "holdings_are_snapshots": True}

    def futu_symbols(self) -> dict:
        cfg = (self.config.get("integrations") or {}).get("futu") or {}
        host = cfg.get("host", "127.0.0.1")
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Futu OpenD 只允许本机连接")
        try:
            from futu import OpenSecTradeContext, TrdEnv, TrdMarket, RET_OK
        except ImportError as error:
            raise ValueError("未安装 Futu OpenAPI SDK") from error
        context = OpenSecTradeContext(host=host, port=int(cfg.get("port", 11111)), filter_trdmarket=TrdMarket.NONE)
        try:
            result, accounts = context.get_acc_list()
            if result != RET_OK:
                raise ValueError("无法读取 Futu 账户，请检查 OpenD 登录")
            codes, skipped = set(), 0
            for _, account in accounts.iterrows():
                if str(account.get("trd_env", "")).upper() != "REAL":
                    continue
                result, positions = context.position_list_query(trd_env=TrdEnv.REAL, acc_id=account["acc_id"], refresh_cache=True)
                if result != RET_OK:
                    raise ValueError("读取 Futu 真实持仓失败")
                for _, row in positions.iterrows():
                    try:
                        qty = float(row.get("qty", 0))
                        if not math.isfinite(qty) or qty <= 0 or str(row.get("position_side", "")).upper() != "LONG":
                            skipped += 1
                            continue
                        code = resolve_identity(str(row.get("code", "")))
                        if code.market in ("US", "HK"):
                            codes.add(code.code)
                    except ValueError:
                        skipped += 1
            return {"codes": sorted(codes), "skipped": skipped, "source": "Futu", "read_only": True}
        finally:
            context.close()
