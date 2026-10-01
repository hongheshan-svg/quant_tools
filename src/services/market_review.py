"""
LLM 大盘复盘（参考 daily_stock_analysis 的 market_analyzer 与 A 股三段式复盘蓝图）

按「趋势结构 → 资金情绪 → 主线板块」复盘当天市场，结论落到进攻/均衡/防守，并给出次日计划。
事实全部来自 market_context（指数、涨跌、量化大盘环境、题材/行业主线、重要快讯）；
护栏：LLM 给出的姿态不能比量化大盘环境更激进（冰点/防守时不会变成进攻），仓位建议与之对应。
每个交易日保存一份（market_review 表），重复生成会覆盖；盘中生成的复盘在收盘后会重新生成。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from loguru import logger

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import MarketReview
from src.services.market_context import build_market_facts
from src.services.report_language import display, language_directive, report_language, tr

STANCE_RANK = {"防守": 0, "均衡": 1, "进攻": 2}
REGIME_MAX_STANCE = {"冰点": "防守", "防守": "防守", "均衡": "均衡", "进攻": "进攻"}
STANCE_POSITION = {"进攻": "7~10 成", "均衡": "4~6 成", "防守": "0~3 成"}
MARKET_CLOSE = "15:00"
REVIEW_ENUMS = '"stance" must be exactly one of 进攻/均衡/防守 (keep the Chinese value verbatim)'

SYSTEM_PROMPT = """你是 A 股短线（打板、连板接力、题材主线）复盘分析师。按三段式框架复盘当天市场，并给出次日计划。

复盘框架：
1. 趋势结构：三大指数是否同向，放量上涨还是缩量下跌，市场处于上升、震荡还是防守阶段
2. 资金情绪：涨跌家数、涨跌停结构、赚钱效应（昨日涨停今日表现）、连板高度、高位股是否分歧
3. 主线板块：题材主线的阶段与持续性、龙头地位、是否有新题材启动、哪些方向在降温
行动框架：进攻（指数共振上行 + 量能放大 + 主线强化）/ 均衡（分化或缩量震荡，控制仓位等待确认）/ 防守（指数转弱 + 亏钱效应扩散，优先风控）

要求：只使用给出的数据，不臆测未提供的信息；结论必须落到仓位、方向和观察点。

仅返回 JSON：
{
  "headline": "一句话总结今天的市场（30字以内）",
  "trend": "趋势结构分析（80字以内）",
  "emotion": "资金情绪分析（80字以内）",
  "main_lines": "主线板块分析（100字以内）",
  "stance": "进攻/均衡/防守",
  "position": "次日建议仓位，如 3~5 成",
  "focus": ["次日重点关注方向或标的（含理由）"],
  "avoid": ["次日回避方向（含理由）"],
  "watch_points": ["次日开盘后需要确认的信号"]
}"""


class MarketReviewService:
    def __init__(self, config: dict | None = None, llm=None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        self._llm = llm

    @property
    def llm(self):
        if self._llm is None:
            from src.analyzers.llm_client import LLMClient

            self._llm = LLMClient(self.config.get("llm", {}))
        return self._llm

    def generate(self, overview: dict | None = None, force: bool = False) -> dict[str, Any]:
        """生成复盘；该交易日已有收盘后的复盘时直接复用（force=True 重新生成）。

        返回 {"trade_date", "stance", "markdown", ...}，失败时含 error。
        """
        lang = report_language(self.config)
        facts = build_market_facts(self.config, overview)
        trade_date = facts.regime.trade_date if facts.regime else datetime.now().strftime("%Y-%m-%d")
        if not force:
            existing = self.get(trade_date)
            if (existing and existing.get("created_at", "") >= f"{trade_date} {MARKET_CLOSE}"
                    and (existing.get("language") or "zh") == lang):
                return existing
        if facts.regime is None and not facts.overview:
            return {"trade_date": trade_date, "error": "没有足够的市场数据，无法复盘"}

        try:
            raw = self.llm.chat_json(user_message=f"复盘日期：{trade_date}\n\n{facts.text()}", system_message=SYSTEM_PROMPT + language_directive(lang, REVIEW_ENUMS))
        except Exception as e:
            logger.error(f"大盘复盘 LLM 调用失败: {e}")
            raw = {}
        if not raw:
            return {"trade_date": trade_date, "error": "AI 未返回有效结果，请检查 AI 设置或稍后重试"}
        result = self._apply_guardrails(raw, facts, trade_date, lang)
        self._save(result)
        return result

    def get(self, trade_date: str | None = None) -> dict[str, Any] | None:
        """某天（默认最近一天）的复盘。"""
        with get_db_session(self.db_path) as session:
            query = session.query(MarketReview)
            if trade_date:
                query = query.filter(MarketReview.trade_date == trade_date)
            row = query.order_by(MarketReview.trade_date.desc()).first()
            return json.loads(row.content_json) if row else None

    @staticmethod
    def _apply_guardrails(raw: dict, facts, trade_date: str, lang: str = "zh") -> dict[str, Any]:
        stance = str(raw.get("stance", "")).strip()
        stance = stance if stance in STANCE_RANK else "均衡"
        guardrails = []
        regime = facts.regime
        if regime is not None:
            cap = REGIME_MAX_STANCE.get(regime.regime, "均衡")
            if STANCE_RANK[stance] > STANCE_RANK[cap]:
                guardrails.append(tr(
                    lang,
                    f"量化大盘环境为「{regime.regime}」（{regime.score:.0f}分），姿态由「{stance}」下调为「{cap}」",
                    f"Quantitative market regime is \"{display(lang, regime.regime)}\" ({regime.score:.0f} pts); "
                    f"stance downgraded from \"{display(lang, stance)}\" to \"{display(lang, cap)}\"",
                ))
                stance = cap
        position = str(raw.get("position", "")).strip() if not guardrails else STANCE_POSITION[stance]
        as_list = lambda v: [str(x) for x in v if x] if isinstance(v, list) else ([str(v)] if v else [])  # noqa: E731
        result = {
            "trade_date": trade_date,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "headline": str(raw.get("headline", "")),
            "trend": str(raw.get("trend", "")),
            "emotion": str(raw.get("emotion", "")),
            "main_lines": str(raw.get("main_lines", "")),
            "stance": stance,
            "position": position or STANCE_POSITION[stance],
            "focus": as_list(raw.get("focus")),
            "avoid": as_list(raw.get("avoid")),
            "watch_points": as_list(raw.get("watch_points")),
            "guardrails": guardrails,
            "regime": regime.summary() if regime is not None else "",
            "language": lang,
        }
        result["markdown"] = render_markdown(result)
        return result

    def _save(self, result: dict[str, Any]) -> None:
        with get_db_session(self.db_path) as session:
            session.query(MarketReview).filter(MarketReview.trade_date == result["trade_date"]).delete()
            session.add(MarketReview(
                trade_date=result["trade_date"], stance=result["stance"], markdown=result["markdown"],
                content_json=json.dumps(result, ensure_ascii=False),
            ))
        logger.info(f"大盘复盘已生成：{result['trade_date']} {result['stance']}")


def render_markdown(result: dict[str, Any]) -> str:
    lang = result.get("language") or "zh"
    if result.get("error"):
        return tr(lang, f"**复盘失败**：{result['error']}", f"**Review failed**: {result['error']}")
    icon = {"进攻": "🔴", "均衡": "🟡", "防守": "🟢"}.get(result["stance"], "")
    lines = [f"**{result['headline']}**" if result.get("headline") else "",
             tr(lang, f"{icon} 次日姿态：**{result['stance']}**，建议仓位 {result['position']}",
                f"{icon} Next-day stance: **{display(lang, result['stance'])}**, suggested position {result['position']}")]
    if result.get("guardrails"):
        lines.append(tr(lang, "> 护栏：", "> Guardrails: ") + "；".join(result["guardrails"]))
    for label, key in ((tr(lang, "趋势结构", "Trend"), "trend"), (tr(lang, "资金情绪", "Sentiment"), "emotion"),
                       (tr(lang, "主线板块", "Main themes"), "main_lines")):
        if result.get(key):
            lines.append(f"**{label}**：{result[key]}")
    for label, key in ((tr(lang, "次日关注", "Focus"), "focus"), (tr(lang, "回避方向", "Avoid"), "avoid"),
                       (tr(lang, "观察要点", "Watch points"), "watch_points")):
        if result.get(key):
            lines.append(f"**{label}**\n" + "\n".join(f"- {x}" for x in result[key]))
    return "\n\n".join(x for x in lines if x)
