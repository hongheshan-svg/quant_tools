"""
自选股决策仪表盘（参考 daily_stock_analysis 的每日自选股分析推送）

对每只自选股做 AI 诊断（收盘后已有当天收盘后的诊断、或盘中 30 分钟内的诊断时直接复用），汇总成一份仪表盘：
- 概览：买入/加仓、持有/观望、减仓/卖出/回避各几只
- 每只：结论、评分、一句话理由、与上一次诊断相比的变化、主要风险和护栏
- 诊断失败的股票单独列出
每天保存一份（watchlist_report 表，重新生成会覆盖），并推送到已启用的渠道。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Callable

from loguru import logger

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import WatchlistReport
from src.services.watchlist import WatchlistService

MARKET_CLOSE = "15:00"
REUSE_MINUTES = 30
BUCKETS = (
    ("🟢", "买入/加仓", {"buy", "add"}),
    ("🟡", "持有/观望", {"hold", "watch", "alert"}),
    ("🔴", "减仓/卖出/回避", {"reduce", "sell", "avoid"}),
)


def bucket_of(action: str) -> tuple[str, str]:
    return next(((icon, label) for icon, label, actions in BUCKETS if action in actions), ("⚪", "其他"))


def _bucket_rank(action: str) -> int:
    return next((i for i, (_, _, actions) in enumerate(BUCKETS) if action in actions), len(BUCKETS))


def reuse_threshold(now: datetime) -> str:
    """复用诊断的最早生成时间：收盘后只复用当天收盘后的诊断，盘中复用 30 分钟内的。"""
    if now.strftime("%H:%M") >= MARKET_CLOSE:
        return f"{now:%Y-%m-%d} {MARKET_CLOSE}"
    return (now - timedelta(minutes=REUSE_MINUTES)).strftime("%Y-%m-%d %H:%M")


def change_text(current: dict[str, Any], previous: dict[str, Any] | None) -> str:
    if not previous or previous.get("error"):
        return "首次诊断"
    parts = []
    if previous.get("action") != current.get("action"):
        parts.append(f"{previous.get('action_label', '')}→{current.get('action_label', '')}")
    delta = (current.get("score") or 0) - (previous.get("score") or 0)
    if delta:
        parts.append(f"评分 {previous.get('score')}→{current.get('score')}")
    return ("较上次（" + previous.get("created_at", "")[5:] + "）：" + "，".join(parts)) if parts else "与上次一致"


def render_dashboard(trade_date: str, items: list[dict[str, Any]], failed: list[dict[str, str]]) -> str:
    counts = {label: 0 for _, label, _ in BUCKETS}
    for it in items:
        _, label = bucket_of(it["action"])
        counts[label] = counts.get(label, 0) + 1
    lines = [
        f"## 🎯 {trade_date} 自选股决策仪表盘",
        f"共分析 {len(items) + len(failed)} 只 | " + " ".join(f"{icon}{label} {counts[label]}" for icon, label, _ in BUCKETS),
        "### 📊 结论摘要",
    ]
    ordered = sorted(items, key=lambda it: (_bucket_rank(it["action"]), -(it["score"] or 0), it["code"]))
    for it in ordered:
        icon, _ = bucket_of(it["action"])
        lines.append(f"- {icon} **{it['name']}({it['code']})**：{it['action_label']}｜评分 {it['score']}｜{it['one_sentence'] or '-'}"
                     f"（{it['change']}）")
    lines.append("### 🔎 个股要点")
    for it in ordered:
        detail = [f"**{it['name']}({it['code']})** {it['action_label']} {it['score']}分"]
        plan = it.get("battle_plan") or {}
        plan_text = "，".join(f"{label}{plan[k]:.2f}" for k, label in (("buy_price", "买入"), ("stop_loss", "止损"), ("target_price", "目标")) if plan.get(k))
        if plan_text:
            detail.append(f"- 价格计划：{plan_text}")
        if it.get("catalysts"):
            detail.append("- 利好：" + "；".join(it["catalysts"][:2]))
        if it.get("risks"):
            detail.append("- 风险：" + "；".join(it["risks"][:3]))
        if it.get("guardrails"):
            detail.append("- 护栏：" + "；".join(it["guardrails"]))
        lines.append("\n".join(detail))
    if failed:
        lines.append("### ⚠️ 未完成\n" + "\n".join(f"- {f['name'] or f['code']}：{f['error']}" for f in failed))
    lines.append(f"\n> 生成时间 {datetime.now():%Y-%m-%d %H:%M}，仅供学习研究，不构成投资建议")
    return "\n\n".join(lines)


class WatchlistReportService:
    def __init__(self, config: dict | None = None, diagnosis=None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        self.workers = max(1, int((self.config.get("watchlist") or {}).get("workers", 3)))
        self._diagnosis = diagnosis

    @property
    def diagnosis(self):
        if self._diagnosis is None:
            from src.services.stock_diagnosis import StockDiagnosisService

            self._diagnosis = StockDiagnosisService(self.config)
        return self._diagnosis

    def run(self, push: bool = True, progress: Callable[[int, int], None] | None = None,
            now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now()
        stocks = WatchlistService(self.config).list()
        if not stocks:
            return {"error": "自选股为空，先在【自选股】页添加", "total": 0}
        threshold = reuse_threshold(now)
        items, failed, done = [], [], 0
        with ThreadPoolExecutor(max_workers=min(self.workers, len(stocks)), thread_name_prefix="watchlist") as pool:
            futures = {pool.submit(self._diagnose_one, s, threshold): s for s in stocks}
            for future in as_completed(futures):
                stock = futures[future]
                try:
                    item = future.result()
                except Exception as e:
                    logger.warning(f"自选股诊断失败 [{stock['code']}]: {e}")
                    item = {"error": str(e)[:100]}
                if item.get("error"):
                    failed.append({"code": stock["code"], "name": stock["name"], "error": item["error"]})
                else:
                    items.append(item)
                done += 1
                if progress:
                    progress(done, len(stocks))

        trade_date = now.strftime("%Y-%m-%d")
        markdown = render_dashboard(trade_date, items, failed)
        self._save(trade_date, markdown, items, failed)
        pushed = self._push(trade_date, markdown) if push else False
        counts = {label: sum(1 for it in items if bucket_of(it["action"])[1] == label) for _, label, _ in BUCKETS}
        logger.info(f"自选股决策仪表盘 {trade_date}：{len(items)} 只完成，{len(failed)} 只失败，推送 {pushed}")
        return {"trade_date": trade_date, "total": len(stocks), "done": len(items), "failed": failed,
                "counts": counts, "markdown": markdown, "pushed": pushed}

    def _diagnose_one(self, stock: dict[str, Any], threshold: str) -> dict[str, Any]:
        code = stock["code"]
        latest = self.diagnosis.latest(code)
        reused = bool(latest and not latest.get("error") and latest.get("created_at", "") >= threshold)
        result = latest if reused else self.diagnosis.diagnose(code, force=True)
        if result.get("error"):
            return {"error": result["error"]}
        history = self.diagnosis.history(code, limit=2)
        previous = history[1] if len(history) > 1 else None
        return {
            "code": code, "name": result.get("name") or stock["name"], "action": result["action"],
            "action_label": result["action_label"], "score": result["score"], "one_sentence": result.get("one_sentence", ""),
            "battle_plan": result.get("battle_plan") or {}, "catalysts": result.get("catalysts") or [],
            "risks": result.get("risks") or [], "guardrails": result.get("guardrails") or [],
            "created_at": result.get("created_at", ""), "reused": reused, "change": change_text(result, previous),
        }

    def _save(self, trade_date: str, markdown: str, items: list, failed: list) -> None:
        with get_db_session(self.db_path) as session:
            session.query(WatchlistReport).filter(WatchlistReport.trade_date == trade_date).delete()
            session.add(WatchlistReport(trade_date=trade_date, markdown=markdown,
                                        summary_json=json.dumps({"items": items, "failed": failed}, ensure_ascii=False)))

    def _push(self, trade_date: str, markdown: str) -> bool:
        from src.notifier import broadcast, enabled_channels

        if not enabled_channels(self.config, "watchlist"):
            return False
        results = broadcast(self.config, f"自选股决策仪表盘 {trade_date}", markdown, kind="watchlist")
        return any(results.values())

    def latest(self) -> dict[str, Any] | None:
        with get_db_session(self.db_path) as session:
            row = session.query(WatchlistReport).order_by(WatchlistReport.trade_date.desc()).first()
            if not row:
                return None
            summary = json.loads(row.summary_json or "{}")
            return {"trade_date": row.trade_date, "markdown": row.markdown, "items": summary.get("items", []),
                    "failed": summary.get("failed", []), "created_at": row.created_at.strftime("%Y-%m-%d %H:%M")}
