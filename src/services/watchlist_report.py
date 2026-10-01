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
from src.services.report_language import display, report_language, tr
from src.services.watchlist import WatchlistService

MARKET_CLOSE = "15:00"
REUSE_MINUTES = 30
BUCKETS = (
    ("🟢", "买入/加仓", {"buy", "add"}),
    ("🟡", "持有/观望", {"hold", "watch", "alert"}),
    ("🔴", "减仓/卖出/回避", {"reduce", "sell", "avoid"}),
)


FUND_TAGS = {"etf": "ETF", "index": "指数"}
BUCKET_LABELS_EN = {"买入/加仓": "Buy/Add", "持有/观望": "Hold/Watch", "减仓/卖出/回避": "Reduce/Sell/Avoid"}


def _title(it: dict[str, Any], lang: str = "zh") -> str:
    """名称(代码)，ETF/指数在后面标注类型。"""
    tag = FUND_TAGS.get(it.get("kind", "stock"))
    if tag and lang == "en":
        tag = "Index" if tag == "指数" else tag
        return f"{it['name']}({it['code']}) [{tag}]"
    return f"{it['name']}({it['code']})" + (f"「{tag}」" if tag else "")


def bucket_of(action: str) -> tuple[str, str]:
    return next(((icon, label) for icon, label, actions in BUCKETS if action in actions), ("⚪", "其他"))


def _bucket_rank(action: str) -> int:
    return next((i for i, (_, _, actions) in enumerate(BUCKETS) if action in actions), len(BUCKETS))


def reuse_threshold(now: datetime) -> str:
    """复用诊断的最早生成时间：收盘后只复用当天收盘后的诊断，盘中复用 30 分钟内的。"""
    if now.strftime("%H:%M") >= MARKET_CLOSE:
        return f"{now:%Y-%m-%d} {MARKET_CLOSE}"
    return (now - timedelta(minutes=REUSE_MINUTES)).strftime("%Y-%m-%d %H:%M")


def change_text(current: dict[str, Any], previous: dict[str, Any] | None, lang: str = "zh") -> str:
    if not previous or previous.get("error"):
        return tr(lang, "首次诊断", "First diagnosis")
    parts = []
    if previous.get("action") != current.get("action"):
        parts.append(f"{display(lang, previous.get('action_label', ''))}→{display(lang, current.get('action_label', ''))}")
    delta = (current.get("score") or 0) - (previous.get("score") or 0)
    if delta:
        parts.append(tr(lang, f"评分 {previous.get('score')}→{current.get('score')}", f"score {previous.get('score')}→{current.get('score')}"))
    if not parts:
        return tr(lang, "与上次一致", "Same as last time")
    return tr(lang, "较上次（" + previous.get("created_at", "")[5:] + "）：" + "，".join(parts),
              "Since last (" + previous.get("created_at", "")[5:] + "): " + ", ".join(parts))


def render_dashboard(trade_date: str, items: list[dict[str, Any]], failed: list[dict[str, str]], lang: str = "zh") -> str:
    counts = {label: 0 for _, label, _ in BUCKETS}
    for it in items:
        _, label = bucket_of(it["action"])
        counts[label] = counts.get(label, 0) + 1
    bucket_name = lambda label: tr(lang, label, BUCKET_LABELS_EN.get(label, label))  # noqa: E731
    lines = [
        tr(lang, f"## 🎯 {trade_date} 自选股决策仪表盘", f"## 🎯 {trade_date} Watchlist Decision Dashboard"),
        tr(lang, f"共分析 {len(items) + len(failed)} 只 | ", f"{len(items) + len(failed)} analyzed | ")
        + " ".join(f"{icon}{bucket_name(label)} {counts[label]}" for icon, label, _ in BUCKETS),
        tr(lang, "### 📊 结论摘要", "### 📊 Summary"),
    ]
    ordered = sorted(items, key=lambda it: (_bucket_rank(it["action"]), -(it["score"] or 0), it["code"]))
    for it in ordered:
        icon, _ = bucket_of(it["action"])
        if lang == "en":
            lines.append(f"- {icon} **{_title(it, lang)}**: {display(lang, it['action_label'])} | Score {it['score']} | "
                         f"{it['one_sentence'] or '-'} ({it['change']})")
        else:
            lines.append(f"- {icon} **{_title(it)}**：{it['action_label']}｜评分 {it['score']}｜{it['one_sentence'] or '-'}"
                         f"（{it['change']}）")
    lines.append(tr(lang, "### 🔎 个股要点", "### 🔎 Stock Details"))
    for it in ordered:
        detail = [tr(lang, f"**{_title(it)}** {it['action_label']} {it['score']}分",
                     f"**{_title(it, lang)}** {display(lang, it['action_label'])} {it['score']} pts")]
        plan = it.get("battle_plan") or {}
        plan_text = tr(lang, "，", ", ").join(
            f"{label}{plan[k]:.2f}" if lang != "en" else f"{label} {plan[k]:.2f}"
            for k, label in (("buy_price", tr(lang, "买入", "Buy")), ("stop_loss", tr(lang, "止损", "Stop loss")),
                             ("target_price", tr(lang, "目标", "Target"))) if plan.get(k))
        if plan_text:
            detail.append(tr(lang, "- 价格计划：", "- Price plan: ") + plan_text)
        if it.get("catalysts"):
            detail.append(tr(lang, "- 利好：", "- Catalysts: ") + "；".join(it["catalysts"][:2]))
        if it.get("risks"):
            detail.append(tr(lang, "- 风险：", "- Risks: ") + "；".join(it["risks"][:3]))
        if it.get("guardrails"):
            detail.append(tr(lang, "- 护栏：", "- Guardrails: ") + "；".join(it["guardrails"]))
        lines.append("\n".join(detail))
    if failed:
        lines.append(tr(lang, "### ⚠️ 未完成", "### ⚠️ Incomplete") + "\n" + "\n".join(f"- {f['name'] or f['code']}：{f['error']}" for f in failed))
    lines.append(tr(lang, f"\n> 生成时间 {datetime.now():%Y-%m-%d %H:%M}，仅供学习研究，不构成投资建议",
                    f"\n> Generated at {datetime.now():%Y-%m-%d %H:%M}. For study and research only; not investment advice"))
    return "\n\n".join(lines)


class WatchlistReportService:
    def __init__(self, config: dict | None = None, diagnosis=None, fund_diagnosis=None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        self.workers = max(1, int((self.config.get("watchlist") or {}).get("workers", 3)))
        self._diagnosis = diagnosis
        self._fund_diagnosis = fund_diagnosis

    @property
    def diagnosis(self):
        if self._diagnosis is None:
            from src.services.stock_diagnosis import StockDiagnosisService

            self._diagnosis = StockDiagnosisService(self.config)
        return self._diagnosis

    @property
    def fund_diagnosis(self):
        """ETF/指数用基金诊断服务（复用规则相同）。"""
        if self._fund_diagnosis is None:
            from src.services.fund_diagnosis import FundDiagnosisService

            self._fund_diagnosis = FundDiagnosisService(self.config)
        return self._fund_diagnosis

    def run(self, push: bool = True, progress: Callable[[int, int], None] | None = None,
            now: datetime | None = None, codes: list[str] | None = None) -> dict[str, Any]:
        """codes 不为空时诊断这些股票（命令行 --stocks），否则诊断自选股。"""
        now = now or datetime.now()
        if codes:
            stocks = self._stocks_of(codes)
        else:
            stocks = WatchlistService(self.config).list()
        if not stocks:
            return {"error": "没有可分析的股票" if codes else "自选股为空，先在【自选股】页添加", "total": 0}
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
        lang = report_language(self.config)
        markdown = render_dashboard(trade_date, items, failed, lang)
        self._save(trade_date, markdown, items, failed)
        pushed = self._push(trade_date, markdown, lang) if push else False
        counts = {label: sum(1 for it in items if bucket_of(it["action"])[1] == label) for _, label, _ in BUCKETS}
        logger.info(f"自选股决策仪表盘 {trade_date}：{len(items)} 只完成，{len(failed)} 只失败，推送 {pushed}")
        return {"trade_date": trade_date, "total": len(stocks), "done": len(items), "failed": failed,
                "counts": counts, "markdown": markdown, "pushed": pushed}

    def _stocks_of(self, codes: list[str]) -> list[dict[str, Any]]:
        """代码列表 → [{code, name, kind}]（去重保序；个股名称从 stock_info/行情库取，ETF/指数从 fund_info/内置指数取）。"""
        from src.database.models import StockDaily, StockInfo
        from src.services.fund_registry import fund_kind, resolve_fund
        from src.utils.stock_code import code_candidates, diagnosis_code

        result = []
        with get_db_session(self.db_path) as session:
            for code in dict.fromkeys(diagnosis_code(c) for c in codes):
                kind = fund_kind(code, self.db_path)
                if kind:
                    fund = resolve_fund(code, self.db_path)
                    result.append({"code": code, "name": (fund or {}).get("name", ""), "kind": kind})
                    continue
                name = session.query(StockInfo.name).filter(StockInfo.code.in_(code_candidates(code))).limit(1).scalar()
                if not name:
                    name = (session.query(StockDaily.name).filter(StockDaily.code.in_(code_candidates(code)))
                            .order_by(StockDaily.trade_date.desc()).limit(1).scalar())
                result.append({"code": code, "name": name or "", "kind": "stock"})
        return result

    def _diagnose_one(self, stock: dict[str, Any], threshold: str) -> dict[str, Any]:
        code = stock["code"]
        kind = stock.get("kind") or "stock"
        service = self.diagnosis if kind == "stock" else self.fund_diagnosis
        latest = service.latest(code)
        reused = bool(latest and not latest.get("error") and latest.get("created_at", "") >= threshold
                      and (latest.get("language") or "zh") == report_language(self.config))
        result = latest if reused else service.diagnose(code, force=True)
        if result.get("error"):
            return {"error": result["error"]}
        history = service.history(code, limit=2)
        previous = history[1] if len(history) > 1 else None
        return {
            "code": code, "kind": kind, "name": result.get("name") or stock["name"], "action": result["action"],
            "action_label": result["action_label"], "score": result["score"], "one_sentence": result.get("one_sentence", ""),
            "battle_plan": result.get("battle_plan") or {}, "catalysts": result.get("catalysts") or [],
            "risks": result.get("risks") or [], "guardrails": result.get("guardrails") or [],
            "created_at": result.get("created_at", ""), "reused": reused,
            "change": change_text(result, previous, report_language(self.config)),
        }

    def _save(self, trade_date: str, markdown: str, items: list, failed: list) -> None:
        with get_db_session(self.db_path) as session:
            session.query(WatchlistReport).filter(WatchlistReport.trade_date == trade_date).delete()
            session.add(WatchlistReport(trade_date=trade_date, markdown=markdown,
                                        summary_json=json.dumps({"items": items, "failed": failed}, ensure_ascii=False)))

    def _push(self, trade_date: str, markdown: str, lang: str = "zh") -> bool:
        from src.notifier import broadcast, enabled_channels

        if not enabled_channels(self.config, "watchlist"):
            return False
        results = broadcast(self.config, tr(lang, f"自选股决策仪表盘 {trade_date}", f"Watchlist Decision Dashboard {trade_date}"), markdown, kind="watchlist")
        return any(results.values())

    def latest(self) -> dict[str, Any] | None:
        with get_db_session(self.db_path) as session:
            row = session.query(WatchlistReport).order_by(WatchlistReport.trade_date.desc()).first()
            if not row:
                return None
            summary = json.loads(row.summary_json or "{}")
            return {"trade_date": row.trade_date, "markdown": row.markdown, "items": summary.get("items", []),
                    "failed": summary.get("failed", []), "created_at": row.created_at.strftime("%Y-%m-%d %H:%M")}
