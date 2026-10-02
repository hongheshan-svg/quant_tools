"""将本地诊断快照转换成统一证据包与研究产物，不额外联网或重新调用模型。"""

from __future__ import annotations

from typing import Any
import math

from src.schemas.research import AnalysisContextPack, ContextBlock, ContextItem, ResearchArtifact
from src.utils.redaction import redact
from src.utils.stock_code import resolve_identity
from src.services.opinion_validity import valid_score

EVIDENCE_LABELS = {
    "quote": "行情", "daily": "日线", "technical": "技术面", "flow": "资金流", "chips": "筹码",
    "earnings": "业绩", "fundamentals": "季度财务", "market": "大盘", "news": "资讯", "notices": "公告",
    "close": "收盘价", "open": "开盘价", "high": "最高价", "low": "最低价", "change_pct": "涨跌幅（%）",
    "volume": "成交量", "amount": "成交额", "trade_date": "行情日期", "bar_count": "日线数量",
    "summary": "摘要", "evidence": "证据", "reports": "财务报告", "dividend": "现金分红",
    "theme": "市场主线", "pe": "市盈率", "pb": "市净率", "source": "来源", "as_of": "截至日期",
    "date": "日期", "risk": "风险", "title": "标题", "report_date": "报告期",
    "price_adjustment": "复权口径", "updated_at": "采集时间",
}


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else [str(value)] if value else []


def _summary(value: Any) -> str:
    if isinstance(value, dict):
        return "；".join(f"{EVIDENCE_LABELS.get(key, key)}：{_summary(item)}" for key, item in value.items()
                         if item not in (None, "", [], {}))
    if isinstance(value, list):
        return "；".join(_summary(item) for item in value)
    return str(value)


def build_context_pack(context: dict, run_log=None) -> dict[str, Any]:
    from src.services.diagnosis_agents import split_sections
    identity = resolve_identity(context["code"])
    quote = context.get("quote") or {}
    quality = context.get("data_quality") or {}
    sections = split_sections(context.get("text", ""))
    steps = {s["name"]: s for s in (run_log.to_dict()["steps"] if run_log else [])}
    pack = AnalysisContextPack(subject={"code": identity.code, "stock_name": context.get("name", ""),
                                       "kind": context.get("kind") or identity.kind, "market": "CN", "currency": "CNY"},
                               phase=context.get("phase") or {}, data_quality=quality)
    specifications = {
        "quote": ("行情", quote, quote.get("source") or "本地日线", quote.get("trade_date")),
        "daily": ("日线", context.get("daily") or {"bar_count": quality.get("bar_count"), "summary": sections.get("近期走势", "")}, "本地日线", quote.get("trade_date")),
        "technical": ("技术面", context.get("technical") or sections.get("技术面", ""), "本地技术指标", quote.get("trade_date")),
        "flow": ("资金流", context.get("flow") or context.get("flow_text"), (context.get("flow") or {}).get("source", "本地资金流"), (context.get("flow") or {}).get("trade_date")),
        "chips": ("筹码", context.get("chip"), (context.get("chip") or {}).get("source"), (context.get("chip") or {}).get("date")),
        "earnings": ("业绩", context.get("earnings_text"), "业绩预告/快报", None),
        "fundamentals": ("季度财务", context.get("fundamentals"), (context.get("fundamentals") or {}).get("source"), (context.get("fundamentals") or {}).get("as_of")),
        "market": ("大盘", {"summary": sections.get("大盘环境", ""), "theme": context.get("role")}, "本地市场结构", None),
        "news": ("资讯", context.get("news_evidence") or [], "本地资讯与联网搜索", None),
        "notices": ("个股新闻公告", context.get("notices_evidence") or [], "东方财富公告", None),
    }
    for key, (label, value, source, as_of) in specifications.items():
        step = steps.get(label) or (steps.get("行情与日线") if key in ("quote", "daily") else None)
        missing = label in quality.get("missing", []) or not has_evidence(value)
        if key == "daily" and not (value.get("bars") or value.get("summary") or value.get("bar_count")):
            missing = True
        status = "missing" if missing else "available"
        if identity.kind != "stock" and key in ("flow", "chips", "earnings", "notices", "fundamentals"):
            status = "not_supported"
        elif step and not step["ok"]:
            status = "fetch_failed"
        elif key == "fundamentals" and value and value.get("status") in ("partial", "stale", "fetch_failed"):
            status = value["status"]
        elif key == "news" and context.get("web_status") == "failed":
            status = "partial" if value else "fetch_failed"
        elif key == "chips" and source == "本地估算":
            status = "estimated"
        elif key in ("quote", "daily", "technical", "chips") and as_of and (context.get("phase") or {}).get("effective_daily_bar_date") and as_of < str(context["phase"]["effective_daily_bar_date"]):
            status = "stale"
        values = value if isinstance(value, dict) else {"evidence" if isinstance(value, list) else "summary": value}
        pack.blocks[key] = ContextBlock(status=status, source=source, as_of=as_of,
                                       items={k: ContextItem(status="missing" if not has_evidence(v) else status, value=v, source=source, as_of=as_of,
                                                              missing_reason="not_collected" if not has_evidence(v) else None) for k, v in values.items()},
                                       limitations=[status] if status != "available" else [])
    return pack.to_safe_dict()


def has_evidence(value) -> bool:
    if isinstance(value, dict):
        return any(has_evidence(v) for k, v in value.items() if k not in {"source", "as_of", "status", "limitations", "errors"})
    if isinstance(value, list):
        return any(has_evidence(v) for v in value)
    return value is not None and value != ""


def local_context_pack(code: str, db_path: str) -> dict:
    """问股和深度研究共用本地事实包；不会触发联网或生成新诊断。"""
    import json
    from datetime import datetime, timedelta
    from src.services.data_query_service import DataQueryService
    from src.database.db import get_db_session
    from src.database.models import ResearchCache, FinanceNews
    identity = resolve_identity(code)
    query = DataQueryService(db_path)
    if identity.kind != "stock":
        from src.collectors.fund_data import get_fund_daily
        bars = get_fund_daily(identity.code, db_path, 60)
    else:
        bars = query.get_stock_daily_history(identity.code, 60)
    quote = bars[-1] if bars else {}
    with get_db_session(db_path) as session:
        cache = session.get(ResearchCache, "fundamentals:" + identity.code)
        try:
            fundamentals = json.loads(cache.payload_json) if cache else None
            if fundamentals and cache.updated_at < datetime.now() - timedelta(days=30):
                fundamentals = {**fundamentals, "status": "stale"}
        except (TypeError, ValueError):
            fundamentals = None
        news = session.query(FinanceNews).filter(FinanceNews.title.contains(identity.code), FinanceNews.news_time >= datetime.now() - timedelta(days=7), FinanceNews.news_time <= datetime.now()).order_by(FinanceNews.news_time.desc()).limit(10).all()
    return build_context_pack({"code": identity.code, "name": quote.get("name", ""), "quote": quote,
        "daily": {"bars": bars}, "fundamentals": fundamentals,
        "data_quality": {"bar_count": len(bars)}, "news_evidence": [{"title": n.title, "source": n.source, "news_time": str(n.news_time or "")} for n in news]})


def build_research_artifact(result: dict, report_id: int | None = None) -> dict[str, Any]:
    identity = resolve_identity(result["code"])
    action = result.get("action") or "watch"
    quality = result.get("data_quality") or {}
    evidence = []
    for key, block in (result.get("context_pack") or {}).get("blocks", {}).items():
        status = block["status"]
        for item_key, item in block.get("items", {}).items():
            if item.get("value") in (None, "", [], {}):
                continue
            entries = item["value"] if key in ("news", "notices") and isinstance(item["value"], list) else [item["value"]]
            for index, entry in enumerate(entries):
                details = entry if isinstance(entry, dict) else {}
                # 来源和日期随每条证据保留；空块不伪装成已经采集的证据。
                evidence.append({"id": f"{key}:{item_key}:{index}", "source_type": key,
                             "title": str(details.get("title") or f"{EVIDENCE_LABELS.get(key, key)} · {EVIDENCE_LABELS.get(item_key, item_key)}"),
                             "source": details.get("source") or item.get("source"), "as_of": str(details.get("published_at") or details.get("published") or details.get("news_time") or details.get("date") or item.get("as_of") or "") or None,
                             "summary": redact(_summary(details.get("summary") or details.get("content") or entry))[:1200],
                             "freshness": "stale" if status == "stale" else "fresh" if item.get("as_of") and status == "available" else "unknown",
                             "quality_level": "usable" if status == "available" else "limited", "metadata": {"status": status}})
    invalidations = [{"id": "reassessment", "category": "manual", "description": result.get("invalidation") or "研究条件或证据发生变化时重新评估"}]
    stop = (result.get("battle_plan") or {}).get("stop_loss")
    try:
        stop = float(stop) if not isinstance(stop, bool) else None
    except (ValueError, TypeError, OverflowError):
        stop = None
    if stop and math.isfinite(stop) and stop > 0:
        invalidations.append({"id": "stop_loss", "category": "price", "description": f"价格触及止损 {stop}", "threshold": stop, "severity": "critical"})
    from src.strategy.data_quality import finite_number
    target = finite_number((result.get("battle_plan") or {}).get("target_price") or (result.get("battle_plan") or {}).get("take_profit"))
    if target is not None and target > 0:
        invalidations.append({"id": "target_price", "category": "price", "description": f"达到目标价 {target} 后复核持有理由", "threshold": target})
    invalidations.extend([
        {"id": "new_evidence", "category": "evidence", "description": "出现与核心论点冲突的新公告、财务修订或重大新闻时复核"},
        {"id": "data_quality", "category": "data_quality", "description": "行情过期、价格口径中断或关键证据采集失败时暂停沿用原结论", "severity": "critical"},
    ])
    artifact = ResearchArtifact(
        artifact_id=f"diagnosis:{report_id}" if report_id else f"diagnosis:{identity.code}:{result.get('created_at', '')}",
        source_report_id=report_id, created_at=result.get("created_at"),
        subject={"stock_code": identity.code, "stock_name": result.get("name", ""), "market": "CN"},
        thesis={"direction": "bullish" if action in ("buy", "add") else "bearish" if action in ("reduce", "sell", "avoid") else "neutral",
                "summary": result.get("one_sentence") or "", "score": valid_score(result.get("score")),
                "confidence": {"高": 0.8, "中": 0.5, "低": 0.2}.get(result.get("confidence")),
                "action": action, "action_label": result.get("action_label"), "horizon": f"{result.get('horizon_days', 5)} trading days",
                "reasons": _strings(result.get("catalysts")), "risks": _strings(result.get("risks"))},
        strategy_synthesis=result.get("strategy_synthesis") or {},
        evidence=evidence, invalidation_conditions=invalidations,
        next_actions=[{"action": "recheck", "label": "复核触发条件", "reason": (result.get("phase_decision") or {}).get("immediate_action", ""),
                       "due_at": (result.get("phase_decision") or {}).get("next_check_time")}],
        data_quality={"overall_score": quality.get("score"), "missing_blocks": quality.get("missing", []),
                      "source_count": len({e["source"] for e in evidence if e["source"]}),
                      "stale_count": sum(e["freshness"] == "stale" for e in evidence),
                      "limitations": [] if evidence else ["legacy_report_without_context_pack"]})
    return redact(artifact.model_dump(mode="json"))
