"""
个股 AI 诊断（参考 daily_stock_analysis 的决策仪表盘：核心结论 + 数据透视 + 情报 + 作战计划）

按打板短线的视角汇总单只股票的上下文（行情、技术面、资金流、筹码、业绩、涨停记录、主线地位、
大盘环境、相关资讯、个股新闻与公告、龙虎榜、模拟盘持仓）并给出数据完整度，交给 LLM 输出结构化结论；
本地日线不足时先联网补齐。代码端再做护栏：
- 操作建议归一为八态 action，缺失时按评分推断
- 评分 < 50 却给出买入/加仓时降级为观望；大盘冰点时不给买入
- 数据质量：核心行情缺失或数据完整度 < 60% 时不给买入，置信度降为低
- 决策稳定性：当日资金大幅净流出时不给买入；与 3 天内上次诊断方向相反（空转多）但评分变化不足 15 分时暂按观望
- 近 30 天公告含立案调查、退市风险警示等严重风险时不给买入
- 价格计划按最新价和涨跌幅限制校验
结果写入 stock_diagnosis 表，同一只股票 30 分钟内重复诊断直接返回上次结果。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from src.analyzers.decision import ACTION_LABELS, BULLISH_ACTIONS, normalize_action, score_to_action
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import (
    DragonTigerBoard,
    FinanceNews,
    LimitUpStock,
    SentimentAnalysis,
    StockDaily,
    StockDiagnosis,
)
from src.trading.price_plan import sanitize_price_plan
from src.utils.stock_code import bare_code, board_of, code_candidates

CACHE_MINUTES = 30
NEWS_DAYS = 3
STOCK_NEWS_LIMIT = 6   # 东方财富个股新闻最多取几条
NOTICE_LIMIT = 8       # 公告最多取几条
LIMIT_UP_DAYS = 10
MIN_BUY_SCORE = 50
MIN_DATA_QUALITY = 60
MIN_DAILY_BARS = 20
FLOW_OUTFLOW_RATIO = -5.0   # 资金净流出占成交额超过 5% 视为与买入矛盾
STABILITY_DAYS = 3
STABILITY_SCORE_DELTA = 15
BEARISH_ACTIONS = frozenset({"reduce", "sell", "avoid"})
# 数据完整度各块权重（合计 100）
DATA_QUALITY_WEIGHTS = {"行情": 20, "日线": 15, "技术面": 10, "资金流": 15, "筹码": 10, "大盘": 15, "资讯": 10, "业绩": 5}

SYSTEM_PROMPT = """你是一位专注 A 股短线（涨停板、连板接力、主线龙头）的交易分析师，负责对单只股票生成【决策仪表盘】。

分析原则：
- 先看大盘环境和情绪周期，再看个股所在主线的阶段与地位，最后看个股自身的涨停质量与技术面
- 主线龙头、封板早、封单大、未炸板的强于跟风股；降温/退潮板块的跟风股以回避为主
- 高位连板和偏离 5 日线过远要提示风险，但涨停股本身远离均线不等于不能参与
- 结论必须落到具体操作（买/不买、仓位、止损），只使用给出的数据，没有的数据不要编造

评分与操作口径（必须一致）：80-100 强烈买入(buy)，60-79 买入(buy)，40-59 观望(watch)，20-39 减仓(reduce)，0-19 卖出(sell)；
不宜参与的用 avoid；已持仓继续拿用 hold，加码用 add。

仅返回 JSON：
{
  "score": 0到100的整数,
  "action": "buy/add/hold/watch/reduce/sell/avoid",
  "confidence": "高/中/低",
  "one_sentence": "一句话结论（30字以内，直接说做什么）",
  "trend_prediction": "强烈看多/看多/震荡/看空/强烈看空",
  "position_advice": {"no_position": "空仓者怎么做", "has_position": "持仓者怎么做"},
  "battle_plan": {"buy_price": 数字或null, "stop_loss": 数字或null, "target_price": 数字或null, "suggested_position": "建议仓位：X成"},
  "catalysts": ["利好1", "利好2"],
  "risks": ["风险1", "风险2"],
  "checklist": [{"item": "检查项（如主线地位/封板质量/技术形态/大盘环境/消息面）", "status": "pass/warn/fail", "note": "说明"}],
  "analysis": "综合分析（100字以内）"
}"""


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if x]
    return [str(value)] if value else []


class StockDiagnosisService:
    """单只股票的 AI 诊断。"""

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

    def diagnose(self, code: str, force: bool = False) -> dict[str, Any]:
        """诊断一只股票；30 分钟内的诊断结果直接复用（force=True 时重新诊断）。"""
        code = bare_code(code)
        if not force:
            cached = self.latest(code, max_age_minutes=CACHE_MINUTES)
            if cached:
                return {**cached, "cached": True}

        context = self.build_context(code)
        if not context["quote"]:
            return {"code": code, "error": "行情库中没有该股票的数据"}
        raw = self.llm.chat_json(user_message=context["text"], system_message=SYSTEM_PROMPT)
        if not raw:
            return {"code": code, "name": context["name"], "error": "AI 未返回有效结果，请检查 AI 设置或稍后重试"}
        previous = self.latest(code, max_age_minutes=STABILITY_DAYS * 24 * 60)
        result = self._apply_guardrails(raw, context, previous)
        self._save(result)
        return result

    def latest(self, code: str, max_age_minutes: int | None = None) -> dict[str, Any] | None:
        with get_db_session(self.db_path) as session:
            query = session.query(StockDiagnosis).filter(StockDiagnosis.code == bare_code(code))
            if max_age_minutes is not None:
                query = query.filter(StockDiagnosis.created_at >= datetime.now() - timedelta(minutes=max_age_minutes))
            row = query.order_by(StockDiagnosis.created_at.desc()).first()
            return json.loads(row.result_json) if row else None

    def history(self, code: str, limit: int = 5) -> list[dict[str, Any]]:
        """最近几次诊断结果（新的在前）。"""
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(StockDiagnosis.result_json).filter(StockDiagnosis.code == bare_code(code))
                .order_by(StockDiagnosis.created_at.desc(), StockDiagnosis.id.desc()).limit(limit).all()
            )
        return [json.loads(r) for (r,) in rows]

    # ---------- 上下文 ----------

    def build_context(self, code: str) -> dict[str, Any]:
        """汇总诊断所需数据，返回 {"name", "quote", "text", ...}；text 为交给 LLM 的完整上下文。"""
        from src.analyzers.market_regime import MarketRegimeAnalyzer
        from src.analyzers.theme_tracker import ThemeTracker
        from src.strategy.tech_score import analyze_technical

        from src.collectors.daily_history import ensure_daily_history
        from src.collectors.fund_flow import describe as describe_flow
        from src.collectors.fund_flow import latest_fund_flow
        from src.collectors.fundamentals import EarningsCache, describe_chips, describe_earnings, earnings_risk, fetch_chip_summary
        from src.collectors.stock_news import get_stock_news

        try:  # 本地日线不足时先联网补齐，技术面和筹码估算都依赖它
            ensure_daily_history(code, self.db_path)
        except Exception as e:
            logger.debug(f"补齐日线失败 [{code}]: {e}")
        cands = code_candidates(code)
        with get_db_session(self.db_path) as session:
            bar_count = session.query(StockDaily.id).filter(StockDaily.code.in_(cands)).count()
            flow = latest_fund_flow(session, code)
            flow_text, flow_ratio = describe_flow(flow), (flow.net_ratio if flow else None)
            bars = (
                session.query(StockDaily).filter(StockDaily.code.in_(cands))
                .order_by(StockDaily.trade_date.desc()).limit(10).all()
            )
            quote = {}
            if bars:
                b = bars[0]
                quote = {
                    "trade_date": b.trade_date, "close": b.close, "change_pct": b.change_pct, "turnover": b.turnover,
                    "amount_yi": round((b.amount or 0) / 1e8, 2), "circ_mv_yi": round((b.circ_mv or 0) / 1e8, 1),
                }
            name = next((b.name for b in bars if b.name), "")
            recent = [f"{b.trade_date} 收{b.close} {b.change_pct:+.2f}%" for b in reversed(bars) if b.close and b.change_pct is not None]

            limit_ups = (
                session.query(LimitUpStock).filter(LimitUpStock.code.in_(cands))
                .order_by(LimitUpStock.trade_date.desc()).limit(LIMIT_UP_DAYS).all()
            )
            limit_up_lines = [
                f"{r.trade_date} {r.continuous_days}板 首封{r.first_limit_time or '-'} 炸板{r.open_count or 0}次 "
                f"封单{(r.seal_amount or 0) / 1e8:.2f}亿 原因:{r.reason or r.sector or '-'}"
                for r in limit_ups
            ]
            name = name or next((r.name for r in limit_ups if r.name), "")

            since = datetime.now() - timedelta(days=NEWS_DAYS)
            news_lines = []
            if name:
                news = (
                    session.query(FinanceNews.title, FinanceNews.source, FinanceNews.collected_at)
                    .filter(FinanceNews.collected_at >= since, FinanceNews.title.contains(name))
                    .order_by(FinanceNews.collected_at.desc()).limit(8).all()
                )
                news_lines = [f"[{src}] {title}" for title, src, _ in news]
            sentiments = (
                session.query(SentimentAnalysis.sentiment, SentimentAnalysis.impact_score, SentimentAnalysis.analysis_reason)
                .filter(SentimentAnalysis.related_stock_code.in_(cands), SentimentAnalysis.created_at >= since)
                .order_by(SentimentAnalysis.created_at.desc()).limit(5).all()
            )
            sentiment_lines = [f"{s} 影响{score or 0:.0f}/10：{(reason or '')[:60]}" for s, score, reason in sentiments]
            dragon = (
                session.query(DragonTigerBoard.trade_date, DragonTigerBoard.reason, DragonTigerBoard.net_amount)
                .filter(DragonTigerBoard.code.in_(cands)).order_by(DragonTigerBoard.trade_date.desc()).limit(3).all()
            )
            dragon_lines = [f"{d} {reason or ''} 净买入{(net or 0) / 1e4:.0f}万" for d, reason, net in dragon]

        tech = analyze_technical(code, self.db_path)
        tracker = ThemeTracker(self.config)
        themes = tracker.analyze_all()
        role = tracker.stock_roles(themes).get(code)
        theme = next((t for t in themes if role and (t.dimension, t.name) == (role["dimension"], role["theme"])), None)
        regime = MarketRegimeAnalyzer(self.config).analyze()
        position = self._position(code)
        chip = fetch_chip_summary(code, self.db_path)
        try:
            earnings = EarningsCache.get(code)
        except Exception as e:
            logger.debug(f"业绩数据获取失败: {e}")
            earnings = None
        try:
            stock_news = get_stock_news(code)
        except Exception as e:
            logger.debug(f"个股新闻/公告获取失败: {e}")
            stock_news = {"news": [], "notices": []}
        seen = set(news_lines)
        news_lines += [f"{n['date'][:10]} [{n['source'] or '东方财富'}] {n['title']}" for n in stock_news["news"][:STOCK_NEWS_LIMIT]
                       if n["title"] not in seen]
        notices = stock_news["notices"]
        notice_lines = [f"{n['date']} {n['title']}" + (f"（风险：{n['risk']}）" if n["risk"] else "") for n in notices[:NOTICE_LIMIT]]
        risk_notices = [n for n in notices if n["risk"]]

        present = {
            "行情": bool(quote), "日线": bar_count >= MIN_DAILY_BARS, "技术面": bool(tech.brief()),
            "资金流": bool(flow_text), "筹码": chip is not None, "大盘": regime.regime != "未知",
            "资讯": bool(news_lines or sentiment_lines or notices), "业绩": earnings is not None,
        }
        data_quality = {
            "score": sum(DATA_QUALITY_WEIGHTS[k] for k, ok in present.items() if ok),
            "missing": [k for k, ok in present.items() if not ok],
            "core_ok": present["行情"] and present["日线"],
            "bar_count": bar_count,
        }

        sections = [
            f"股票：{name}({code}) {board_of(code)}",
            "【行情】" + self._quote_text(quote),
            "【近期走势】" + ("；".join(recent) if recent else "暂无"),
            f"【技术面】{tech.brief() or '数据不足'}" + (f"；利好信号：{'、'.join(tech.reasons)}" if tech.reasons else ""),
            f"【资金流】{flow_text or '暂无'}",
            f"【筹码】{describe_chips(chip) or '暂无'}",
            f"【业绩】{describe_earnings(earnings) or '近期无业绩预告/快报'}",
            "【近期涨停】" + ("；".join(limit_up_lines) if limit_up_lines else "近期无涨停"),
            "【主线地位】" + (f"{role['role']}，所属{role['dimension']}{theme.brief()}" if role and theme else "不在近期涨停主线中"),
            f"【大盘环境】{regime.summary()}",
            "【相关资讯】" + ("；".join(news_lines) if news_lines else "近期无相关资讯"),
            "【近 30 天公告】" + ("；".join(notice_lines) if notice_lines else "无"),
            "【AI舆情】" + ("；".join(sentiment_lines) if sentiment_lines else "无"),
            "【龙虎榜】" + ("；".join(dragon_lines) if dragon_lines else "近期未上榜"),
            "【持仓】" + (
                f"模拟盘持有 {position['quantity']} 股，成本 {position['avg_cost']:.2f}，止损 {position['stop_loss']:.2f}，目标 {position['target_price']:.2f}"
                if position else "未持仓"
            ),
            f"【数据完整度】{data_quality['score']}%" + (f"（缺少：{'、'.join(data_quality['missing'])}）" if data_quality["missing"] else ""),
        ]
        return {
            "code": code, "name": name, "quote": quote, "tech": tech, "role": role, "regime": regime,
            "position": position, "text": "\n".join(sections), "data_quality": data_quality,
            "flow_text": flow_text, "flow_ratio": flow_ratio, "chip": chip,
            "earnings_text": describe_earnings(earnings), "earnings_risk": earnings_risk(earnings),
            "risk_notices": risk_notices,
        }

    @staticmethod
    def _quote_text(quote: dict[str, Any]) -> str:
        """最新行情一句话，缺失的字段直接省略。"""
        if not quote or quote.get("close") is None:
            return "暂无"
        parts = [f"{quote['trade_date']} 收盘 {quote['close']}" + (f"（{quote['change_pct']:+.2f}%）" if quote.get("change_pct") is not None else "")]
        if quote.get("amount_yi"):
            parts.append(f"成交 {quote['amount_yi']} 亿")
        if quote.get("turnover"):
            parts.append(f"换手 {quote['turnover']}%")
        if quote.get("circ_mv_yi"):
            parts.append(f"流通市值 {quote['circ_mv_yi']} 亿")
        return "，".join(parts)

    def _position(self, code: str) -> dict[str, Any] | None:
        try:
            from src.trading.execution_service import ExecutionService

            snapshot = ExecutionService(self.config).get_trading_snapshot(order_limit=1)
            return next((p for p in snapshot["positions"] if bare_code(p["code"]) == code), None)
        except Exception as e:
            logger.debug(f"读取模拟盘持仓失败: {e}")
            return None

    # ---------- 护栏 ----------

    def _apply_guardrails(self, raw: dict, context: dict, previous: dict | None = None) -> dict[str, Any]:
        try:
            score = max(0.0, min(100.0, float(raw.get("score", 50))))
        except (TypeError, ValueError):
            score = 50.0
        action = normalize_action(str(raw.get("action", ""))) or score_to_action(score)
        guardrails: list[str] = []
        if action in BULLISH_ACTIONS and score < MIN_BUY_SCORE:
            guardrails.append(f"评分 {score:.0f} 与「{ACTION_LABELS[action]}」不一致，降级为观望")
            action = "watch"
        regime = context["regime"]
        if action in BULLISH_ACTIONS and regime.regime == "冰点":
            guardrails.append("大盘处于冰点，暂停开新仓，降级为观望")
            action = "watch"
        elif action in BULLISH_ACTIONS and regime.regime == "防守":
            guardrails.append(f"大盘防守，新开仓仓位按 ×{regime.position_factor:.1f} 控制")

        confidence = str(raw.get("confidence", ""))
        quality = context["data_quality"]
        if action in BULLISH_ACTIONS and not quality["core_ok"]:
            guardrails.append(f"行情或日线数据不足（日线 {quality['bar_count']} 根），无法确认买点，降级为观望")
            action = "watch"
        if quality["score"] < MIN_DATA_QUALITY:
            confidence = "低"
            if action in BULLISH_ACTIONS:
                guardrails.append(f"数据完整度 {quality['score']}%（缺少{'、'.join(quality['missing'])}），不足以支撑买入，降级为观望")
                action = "watch"
        flow_ratio = context.get("flow_ratio")
        if action in BULLISH_ACTIONS and flow_ratio is not None and flow_ratio <= FLOW_OUTFLOW_RATIO:
            guardrails.append(f"资金净流出占成交额 {abs(flow_ratio):.1f}%，与买入建议矛盾，降级为观望")
            action = "watch"
        severe = next((n for n in context.get("risk_notices") or [] if n.get("severe")), None)
        if action in BULLISH_ACTIONS and severe:
            guardrails.append(f"近 30 天公告含「{severe['risk']}」（{severe['date']} {severe['title'][:40]}），不建议买入，降级为观望")
            action = "watch"
        if previous and not previous.get("error"):
            prev_action, prev_score = previous.get("action"), float(previous.get("score") or 0)
            flipped_up = action in BULLISH_ACTIONS and prev_action in BEARISH_ACTIONS
            flipped_down = action in BEARISH_ACTIONS and prev_action in BULLISH_ACTIONS
            if (flipped_up or flipped_down) and abs(score - prev_score) < STABILITY_SCORE_DELTA:
                note = f"与 {previous.get('created_at')} 的诊断（{previous.get('action_label')}，{prev_score:.0f}分）方向相反，但评分变化不足 {STABILITY_SCORE_DELTA} 分"
                if flipped_up:
                    guardrails.append(note + "，暂按观望处理，避免反复")
                    action = "watch"
                else:
                    guardrails.append(note + "，风险优先，保留减仓/回避建议")

        plan_raw = raw.get("battle_plan") or {}
        plan = sanitize_price_plan(
            context["code"], context["name"], (context["quote"] or {}).get("close"),
            plan_raw.get("buy_price"), plan_raw.get("stop_loss"), plan_raw.get("target_price"),
        )
        checklist = [c for c in raw.get("checklist") or [] if isinstance(c, dict)]
        return {
            "code": context["code"],
            "name": context["name"],
            "trade_date": (context["quote"] or {}).get("trade_date", ""),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "score": round(score),
            "action": action,
            "action_label": ACTION_LABELS[action],
            "confidence": confidence,
            "one_sentence": str(raw.get("one_sentence", "")),
            "trend_prediction": str(raw.get("trend_prediction", "")),
            "position_advice": raw.get("position_advice") if isinstance(raw.get("position_advice"), dict) else {},
            "battle_plan": {
                "buy_price": plan.entry_price, "stop_loss": plan.stop_loss, "target_price": plan.target_price,
                "suggested_position": str(plan_raw.get("suggested_position", "")),
            },
            "catalysts": _as_list(raw.get("catalysts")),
            "risks": _as_list(raw.get("risks")) + [f"技术面：{r}" for r in context["tech"].risks]
            + ([f"业绩：{context['earnings_risk']}"] if context.get("earnings_risk") else [])
            + [f"公告：{n['date']} {n['title']}" for n in (context.get("risk_notices") or [])[:3]],
            "checklist": checklist,
            "analysis": str(raw.get("analysis", "")),
            "guardrails": guardrails,
            "theme_role": context["role"] or {},
            "market_regime": regime.summary(),
            "data_quality": {k: quality[k] for k in ("score", "missing")},
            "fund_flow": context.get("flow_text", ""),
            "chips": context.get("chip") or {},
            "earnings": context.get("earnings_text", ""),
        }

    def _save(self, result: dict[str, Any]) -> None:
        with get_db_session(self.db_path) as session:
            session.add(StockDiagnosis(
                code=result["code"], name=result["name"], trade_date=result["trade_date"], action=result["action"],
                score=result["score"], result_json=json.dumps(result, ensure_ascii=False),
            ))


def render_markdown(result: dict[str, Any]) -> str:
    """诊断结果转为 markdown，供桌面端显示。"""
    if result.get("error"):
        return f"**诊断失败**：{result['error']}"
    icon = {"buy": "🟢", "add": "🟢", "hold": "🟡", "watch": "🟡", "reduce": "🟠", "sell": "🔴", "avoid": "🔴"}.get(result["action"], "⚪")
    lines = [
        f"## {icon} {result['name']}({result['code']})：{result['action_label']}｜评分 {result['score']}｜信心 {result['confidence'] or '-'}",
        f"**{result['one_sentence']}**" if result["one_sentence"] else "",
        f"诊断时间 {result['created_at']}（行情 {result['trade_date']}）" + ("，复用 30 分钟内的结果" if result.get("cached") else ""),
    ]
    quality = result.get("data_quality") or {}
    if quality:
        lines.append(f"数据完整度 {quality['score']}%" + (f"（缺少：{'、'.join(quality['missing'])}）" if quality.get("missing") else ""))
    if result["guardrails"]:
        lines.append("> 护栏：" + "；".join(result["guardrails"]))
    advice = result["position_advice"]
    if advice:
        lines += ["### 操作建议", f"- 空仓：{advice.get('no_position', '-')}", f"- 持仓：{advice.get('has_position', '-')}"]
    plan = result["battle_plan"]
    plan_items = [f"{label} {plan[key]:.2f}" for key, label in (("buy_price", "买入"), ("stop_loss", "止损"), ("target_price", "目标")) if plan.get(key)]
    if plan_items or plan.get("suggested_position"):
        lines += ["### 作战计划", "- " + "，".join(plan_items + ([plan["suggested_position"]] if plan.get("suggested_position") else []))]
    if result["theme_role"]:
        role = result["theme_role"]
        lines.append(f"**主线地位**：{role['theme']}（{role['phase']}）{role['role']}")
    lines.append(f"**大盘**：{result['market_regime']}")
    from src.collectors.fundamentals import describe_chips

    for label, text in (("资金", result.get("fund_flow")), ("筹码", describe_chips(result.get("chips"))), ("业绩", result.get("earnings"))):
        if text:
            lines.append(f"**{label}**：{text}")
    if result["catalysts"]:
        lines += ["### 利好催化", *[f"- {c}" for c in result["catalysts"]]]
    if result["risks"]:
        lines += ["### 风险提示", *[f"- {r}" for r in result["risks"]]]
    if result["checklist"]:
        mark = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
        lines += ["### 检查清单", *[f"- {mark.get(c.get('status'), '•')} {c.get('item', '')}：{c.get('note', '')}" for c in result["checklist"]]]
    if result["analysis"]:
        lines += ["### 综合分析", result["analysis"]]
    lines.append("\n> 仅供学习研究，不构成投资建议")
    return "\n\n".join(line for line in lines if line)
