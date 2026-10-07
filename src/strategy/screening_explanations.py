"""选股理由是可核对的观测、规则推断与缺失证据，不能把规则命中当作催化事实。"""

from datetime import date
from src.services.data_freshness import iso_timestamp


def explanations(pick, features, trade_date):
    blocks = (pick.context_pack or {}).get("blocks") or {}
    quote = blocks.get("quote") or {}
    def item(text, kind, source_type, source=None, observed=None, fetched=None, status="available", **extra):
        return {"text": text, "kind": kind, "source_type": source_type, "source": source,
                "provider_timestamp": iso_timestamp(observed), "fetched_at": iso_timestamp(fetched), "status": status, **extra}
    selected = [item(reason, "inferred", "strategy_rule", "+".join(pick.strategies), data_date=trade_date) for reason in pick.reasons]
    for field, label, unit in (("close", "收盘价", "元"), ("change_pct", "当日涨幅", "%"), ("amount", "成交额", "元"), ("vol_ratio", "成交额量比", "倍")):
        value = getattr(features, field, None)
        if value is not None:
            selected.append(item(f"{label} {value:.4g}{unit}", "observed", "quote" if field != "vol_ratio" else "derived_technical", quote.get("source"), quote.get("provider_timestamp"), quote.get("fetched_at"), data_date=trade_date, value=value))
    now = []
    for key in ("news", "notices"):
        block = blocks.get(key) or {}
        values = ((block.get("items") or {}).get("evidence") or {}).get("value") or []
        for evidence in values[:10]:
            if not isinstance(evidence, dict):
                continue
            observed = evidence.get("published_at") or evidence.get("news_time") or evidence.get("date") or evidence.get("published")
            day = str(observed or "")[:10]
            try:
                age = (date.fromisoformat(trade_date) - date.fromisoformat(day)).days
            except ValueError:
                age = None
            status = "unknown" if age is None else "invalid_date" if age < 0 else "stale" if age > 7 else block.get("status", "unknown")
            now.append(item(evidence.get("title") or "未命名资讯", "unknown" if age is None or age < 0 else "observed", key,
                            evidence.get("source") or block.get("source"), observed,
                            evidence.get("fetched_at") or evidence.get("collected_at"), status,
                            data_date=day or None, age_days=age, url=evidence.get("url"), evidence_id=evidence.get("id")))
    if not now:
        now.append(item("未取得具有明确发布时间的近期催化证据", "unknown", "news", status=(blocks.get("news") or {}).get("status", "missing")))
    flow = blocks.get("flow") or {}
    inflow = ((flow.get("items") or {}).get("net_inflow") or {}).get("value")
    if inflow is not None:
        now.append(item(f"资金净流入 {inflow:.4g} 元", "observed", "flow", flow.get("source"), flow.get("provider_timestamp"), flow.get("fetched_at"), flow.get("status", "unknown"), value=inflow, data_date=flow.get("as_of")))
    for flag in pick.risk_flags:
        selected.append(item(flag, "inferred", "risk_rule", "screening_pipeline", status="partial"))
    return selected, now
