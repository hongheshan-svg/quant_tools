"""
个股研究聚合（对齐 daily_stock_analysis 的 GET /stocks/{code}/profile）：个股页一次取齐本地已有的
行情、最近诊断、决策信号、持仓和盯盘信息，不用在浏览器里并发拼多个接口。

- 只读本地数据、不联网（K 线、新闻公告仍走各自接口），所以很快
- 每块独立：status 为 fresh（有可用数据）/ partial（可用但有明确限制）/ unavailable（没有数据），
  limitations 给出稳定的原因代码；某一块出错只影响这一块
- ETF、指数不能交易，没有持仓和自定义提醒规则
"""

from __future__ import annotations

from typing import Any, Callable

from loguru import logger
from sqlalchemy import func

from src.database.db import get_db_session
from src.database.models import FundDaily, StockDaily
from src.utils.stock_code import bare_code, code_candidates
from src.utils.stock_code import resolve_identity

FRESH, PARTIAL, UNAVAILABLE = "fresh", "partial", "unavailable"
ACTIVE_SIGNAL_LIMIT = 5


def _block(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except Exception as e:
        logger.warning(f"个股聚合的数据块出错: {e}")
        return {"status": UNAVAILABLE, "limitations": ["error"]}


class StockProfileService:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.config = pipeline.config
        self.db_path = pipeline.db_path

    def build(self, code: str, history_days: int = 90) -> dict[str, Any]:
        from src.services.fund_registry import resolve_fund

        code = resolve_identity(code).code
        fund = resolve_fund(code, self.db_path)
        kind = fund["kind"] if fund else "stock"
        canonical = fund["code"] if fund else bare_code(code)
        blocks = {
            "quote": _block(lambda: self._quote(canonical, kind)),
            "research": _block(lambda: self._research(canonical)),
            "signals": _block(lambda: self._signals(canonical)),
            "portfolio": _block(lambda: self._portfolio(canonical, kind)),
            "monitors": _block(lambda: self._monitors(canonical, kind)),
            "history": _block(lambda: self._history(canonical, history_days)),
            "intelligence": _block(lambda: self._intelligence(canonical)),
        }
        counts = {s: sum(1 for b in blocks.values() if b["status"] == s) for s in (FRESH, PARTIAL, UNAVAILABLE)}
        return {"code": canonical, "kind": kind, "name": blocks["quote"].get("name") or (fund or {}).get("name", ""),
                **blocks, "evidence_quality": counts}

    # ---- 各数据块 ----

    def _quote(self, code: str, kind: str) -> dict[str, Any]:
        with get_db_session(self.db_path) as session:
            if kind == "stock":
                row = (session.query(StockDaily).filter(StockDaily.code.in_(code_candidates(code)))
                       .order_by(StockDaily.trade_date.desc()).first())
                latest_market = session.query(func.max(StockDaily.trade_date)).scalar()
            else:
                row = session.query(FundDaily).filter(FundDaily.code == code).order_by(FundDaily.trade_date.desc()).first()
                latest_market = None
            if row is None:
                return {"status": UNAVAILABLE, "limitations": ["no_quote"]}
            data = {"trade_date": row.trade_date, "close": row.close, "change_pct": row.change_pct}
            if kind == "stock":
                data.update(source=row.source, price_adjustment=row.price_adjustment,
                            updated_at=row.updated_at.isoformat(timespec="seconds") if row.updated_at else None)
            name = row.name or ""
        if kind == "stock" and not name:
            from src.services.stock_search import StockSearch

            hit = next((r for r in StockSearch(self.db_path).search(code, limit=3) if r["code"] == code), None)
            name = hit["name"] if hit else ""
        # 比全市场最新的行情日早（停牌或只补齐到更早的日线），可用但不是最新
        stale = bool(latest_market and data["trade_date"] < latest_market)
        return {"status": PARTIAL if stale else FRESH, "limitations": ["stale_quote"] if stale else [], "data": data, "name": name}

    def _research(self, code: str) -> dict[str, Any]:
        latest = self.pipeline.latest_diagnosis(code)
        if not latest or latest.get("error"):
            return {"status": UNAVAILABLE, "limitations": ["no_diagnosis"]}
        data = {k: latest.get(k) for k in ("diagnosis_id", "action", "action_label", "score", "created_at", "trade_date", "one_sentence")}
        from src.services.research_artifact import build_research_artifact
        data["structured_report"] = build_research_artifact({"code": code, "name": "", **latest}, latest.get("diagnosis_id"))
        data["context_pack"] = latest.get("context_pack")
        return {"status": FRESH, "limitations": [], "data": data}

    def _history(self, code: str, days: int) -> dict:
        records = self.pipeline.list_diagnoses(code=code, days=days, limit=20)
        return {"status": FRESH if records["items"] else UNAVAILABLE,
                "limitations": [] if records["items"] else ["no_history"],
                "data": {"recent_reports": records["items"], "total": records["total"], "history_days": days}}

    def _intelligence(self, code: str) -> dict:
        from src.services.intelligence import IntelligenceService
        rows = IntelligenceService(self.config).items(symbol=code, limit=20)
        return {"status": FRESH if rows else UNAVAILABLE, "limitations": [] if rows else ["no_intelligence"], "data": {"items": rows}}

    def _signals(self, code: str) -> dict[str, Any]:
        from src.services.decision_signals import DecisionSignalService

        service = DecisionSignalService(self.config)
        active = service.list(status="active", code=code, limit=ACTIVE_SIGNAL_LIMIT)
        rows = active.get("items", []) if isinstance(active, dict) else active
        review = service.review(code)
        if not rows and not review.get("samples"):
            return {"status": UNAVAILABLE, "limitations": ["no_signal"]}
        keep = ("id", "action", "action_label", "status", "trade_date", "expires_on", "stop_loss", "target_price", "ret_1d", "ret_3d")
        return {"status": FRESH, "limitations": [],
                "data": {"active": [{k: r.get(k) for k in keep} for r in rows],
                         "review": {k: review.get(k) for k in ("samples", "hit_rate", "avg_ret", "text")}}}

    def _portfolio(self, code: str, kind: str) -> dict[str, Any]:
        if kind != "stock":
            return {"status": UNAVAILABLE, "limitations": ["not_tradable"]}
        from src.services.real_portfolio import RealPortfolioService
        from src.trading.execution_service import ExecutionService

        holdings = []
        limitations = []
        try:
            paper = ExecutionService(self.config).get_trading_snapshot()
            for p in paper.get("positions", []):
                if bare_code(p.get("code", "")) == code:
                    holdings.append({"source": "paper", "label": "模拟盘", **_position_fields(p)})
        except Exception as e:
            logger.debug(f"读取模拟盘持仓失败: {e}")
            limitations.append("paper_unavailable")
        try:
            for p in RealPortfolioService(self.config).positions():
                if bare_code(p.get("code", "")) == code:
                    holdings.append({"source": "real", "label": "实盘", "accounts": p.get("accounts", []), **_position_fields(p)})
        except Exception as e:
            logger.debug(f"读取实盘持仓失败: {e}")
            limitations.append("real_unavailable")
        # 没持有也是有效信息（fresh）；只有模拟盘、实盘都读不到时才是 unavailable
        status = UNAVAILABLE if len(limitations) == 2 else PARTIAL if limitations else FRESH
        return {"status": status, "limitations": limitations, "data": {"held": bool(holdings), "holdings": holdings}}

    def _monitors(self, code: str, kind: str) -> dict[str, Any]:
        from src.services.watchlist import WatchlistService

        in_watchlist = WatchlistService(self.config).contains(code)
        alerts = self.config.get("alerts") or {}
        rules = [] if kind != "stock" else [
            {"type": r.get("type"), "text": _rule_text(r), "note": r.get("note") or ""}
            for r in alerts.get("rules") or []
            if isinstance(r, dict) and bare_code(str(r.get("code") or "")) == code and r.get("enabled", True) is not False
        ]
        extra = kind == "stock" and code in {bare_code(str(c)) for c in alerts.get("watchlist") or []}
        return {"status": FRESH, "limitations": [] if kind == "stock" else ["fund_no_alerts"],
                "data": {"in_watchlist": in_watchlist, "alert_rules": rules, "alert_watch": extra}}


def _position_fields(p: dict[str, Any]) -> dict[str, Any]:
    out = {k: p.get(k) for k in ("quantity", "available_quantity", "avg_cost", "market_price", "market_value",
                                 "unrealized_pnl", "stop_loss", "target_price")}
    cost, price = out.get("avg_cost") or 0, out.get("market_price") or 0
    out["unrealized_pnl_pct"] = round((price / cost - 1) * 100, 2) if cost and price else None
    return out


def _rule_text(rule: dict[str, Any]) -> str:
    """提醒规则的简短描述，如「价格突破：上破，价格（元） 1800」"""
    from src.services.alert_service import RULE_TYPES

    spec = RULE_TYPES.get(str(rule.get("type") or ""))
    if not spec:
        return str(rule.get("type") or "")
    parts = []
    for field in spec.get("fields", []):
        value = rule.get(field["key"], field.get("default"))
        if value in (None, ""):
            continue
        if field.get("type") == "select":
            value = dict(field.get("options") or []).get(value, value)
            parts.append(str(value))
        else:
            parts.append(f"{field['label']} {value}")
    return spec["label"] + ("：" + "，".join(parts) if parts else "")
