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
- 多智能体（diagnosis.mode = standard/full）：技术面、情报（、风险）分析员先各自给观点，决策员综合；
  分析员有分歧时信心最高为「中」
- 历史校准（diagnosis.calibration）：近 90 天诊断的事后准确率写进提示词；看多诊断的 3 日准确率低于 45%
  （至少 10 次）时，买入建议的信心下调一档
- 价格计划按最新价和涨跌幅限制校验
- 决策信号（diagnosis.signal_review）：买入/加仓/减仓/卖出/回避的诊断保存后生成决策信号（见 decision_signals），
  该代码历史信号复盘（样本 ≥ 3）写进提示词，判断偏乐观/偏悲观时要相应调整信心
结果写入 stock_diagnosis 表，同一只股票 30 分钟内重复诊断直接返回上次结果。
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import or_

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
from src.analyzers.attribution import normalize_attribution
from src.services import market_phase
from src.services.run_log import RunLog
from src.trading.price_plan import sanitize_price_plan
from src.utils.stock_code import bare_code, board_of, code_candidates, name_variants, normalize_name

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
MIN_CALIBRATION_SAMPLES = 10       # 历史校准：看多诊断至少验证过 10 次才生效
LOW_CALIBRATION_ACCURACY = 45.0    # 看多诊断 3 日准确率低于该值时下调买入信心
DEFAULT_HORIZON = 5
MAX_HORIZON = 20
MAX_INVALIDATION_LEN = 200
BEARISH_ACTIONS = frozenset({"reduce", "sell", "avoid"})
# 数据完整度各块权重（合计 100）
DATA_QUALITY_WEIGHTS = {"行情": 20, "日线": 15, "技术面": 10, "资金流": 15, "筹码": 10, "大盘": 15, "资讯": 10, "业绩": 5}

SYSTEM_PROMPT = """你是一位专注 A 股短线（涨停板、连板接力、主线龙头）的交易分析师，负责对单只股票生成【决策仪表盘】。

分析原则：
- 先看【市场阶段】：盘前/非交易日给开盘计划，盘中给当下可执行动作和观察条件，盘后按完整交易日复盘
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
  "invalidation": "失效条件：出现什么情况说明判断错了（如跌破某价、放量滞涨，40字以内）",
  "horizon_days": 观察期，1到20的整数（交易日数，通常 3-5）,
  "phase_decision": {"trading_window": "当前阶段的操作窗口（如开盘后 30 分钟观察承接）", "immediate_action": "现在立刻做什么（盘前/非交易日不能是立即买卖）", "watch_conditions": ["触发条件1", "触发条件2"], "next_check_time": "下次检查时间（如 10:00、下一交易日 9:25）"},
  "signal_attribution": {"technical": 技术面贡献0-100, "news": 资讯情绪贡献0-100, "fundamentals": 基本面贡献0-100, "market": 大盘环境贡献0-100, "strongest_bullish": "最强看多信号", "strongest_bearish": "最强看空信号"},
  "analysis": "综合分析（100字以内）"
}"""


def valuation_text(pe: float | None, pb: float | None) -> str:
    """市盈率为负表示亏损。"""
    parts = []
    if pe:
        parts.append("亏损（市盈率为负）" if pe < 0 else f"市盈率 {pe:.1f}")
    if pb:
        parts.append(f"市净率 {pb:.2f}")
    return "，".join(parts)


def _horizon(value: Any) -> int:
    """观察期（交易日）限制在 1~20，缺失或非法为 5。"""
    try:
        days = int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_HORIZON
    return days if 1 <= days <= MAX_HORIZON else DEFAULT_HORIZON


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if x]
    return [str(value)] if value else []


def _step_factory(run_log: RunLog | None):
    """run_log 为 None 时返回什么都不记录的 step。"""
    if run_log is not None:
        return run_log.step

    @contextmanager
    def _noop(name: str):
        yield _NoopStep()

    return _noop


class _NoopStep:
    detail = ""


def note_guardrail_change(run_log: RunLog, raw: dict, result: dict[str, Any]) -> None:
    """护栏把原始建议降级时记一条说明。"""
    original = normalize_action(str(raw.get("action", "")))
    final = result.get("action")
    if original and final and original != final:
        reasons = "；".join(result.get("guardrails") or [])
        run_log.note(f"{ACTION_LABELS.get(original, original)}降为{ACTION_LABELS.get(final, final)}：{reasons}"[:200])


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

    def diagnose(self, code: str, force: bool = False, skills: list[str] | None = None) -> dict[str, Any]:
        """诊断一只股票；30 分钟内的诊断结果直接复用（force=True 时重新诊断）。"""
        code = bare_code(code)
        if not force:
            cached = self.latest(code, max_age_minutes=CACHE_MINUTES)
            if cached:
                return {**cached, "cached": True}

        from src.services.diagnosis_agents import DECISION_ADDENDUM, disagreement, opinions_text, run_analysts

        run_log = RunLog()
        context = self.build_context(code, run_log)
        if not context["quote"]:
            return {"code": code, "error": "行情库中没有该股票的数据"}
        cfg = self.config.get("diagnosis") or {}
        calibration = self._calibration() if cfg.get("calibration", True) else {}
        model = str((getattr(self.llm, "primary_cfg", None) or {}).get("model") or "")
        analyst_start = time.perf_counter()
        opinions = run_analysts(self.llm, context["text"], str(cfg.get("mode", "single")))
        if opinions:
            run_log.llm("分析员", model, True, (time.perf_counter() - analyst_start) * 1000)
        conflict = disagreement(opinions)
        message = context["text"]
        history_line = self._calibration_line(calibration, code)
        if history_line:
            message += "\n" + history_line
        if opinions:
            message += "\n" + opinions_text(opinions, conflict)
        consult_start = time.perf_counter()
        skill_opinions, skill_consensus, consult_text = self._consult_skills(context, skills)
        if skill_opinions:
            names = "、".join(o["display_name"] for o in skill_opinions)
            run_log.llm(f"策略会诊（{names}）", model, True, (time.perf_counter() - consult_start) * 1000)
        if consult_text:
            message += "\n" + consult_text
        decision_start = time.perf_counter()
        raw = self.llm.chat_json(user_message=message, system_message=SYSTEM_PROMPT + (DECISION_ADDENDUM if opinions else ""))
        run_log.llm("决策", model, bool(raw), (time.perf_counter() - decision_start) * 1000)
        if not raw:
            return {"code": code, "name": context["name"], "error": "AI 未返回有效结果，请检查 AI 设置或稍后重试"}
        previous = self.latest(code, max_age_minutes=STABILITY_DAYS * 24 * 60)
        context = {**context, "opinions": opinions, "disagreement": conflict, "calibration": calibration}
        result = self._apply_guardrails(raw, context, previous)
        result["skill_opinions"], result["skill_consensus"] = skill_opinions, skill_consensus
        note_guardrail_change(run_log, raw, result)
        result["run_log"] = run_log.to_dict()
        diagnosis_id = self._save(result)
        self._record_opinions(result, diagnosis_id)
        self._record_signal(result, diagnosis_id)
        return result

    def _record_signal(self, result: dict[str, Any], diagnosis_id: int | None) -> None:
        """诊断保存后生成决策信号；异常只记日志，不影响诊断。"""
        try:
            from src.services.decision_signals import DecisionSignalService

            DecisionSignalService(self.config).record_from_diagnosis(result, diagnosis_id)
        except Exception as e:
            logger.warning(f"生成决策信号失败 [{result.get('code')}]: {e}")

    def _signal_review_section(self, code: str) -> str:
        """历史信号复盘段落；关闭、样本不足或出错时返回空串。"""
        if not (self.config.get("diagnosis") or {}).get("signal_review", True):
            return ""
        try:
            from src.services.decision_signals import DecisionSignalService

            review = DecisionSignalService(self.config).review(code)
        except Exception as e:
            logger.debug(f"读取历史信号复盘失败 [{code}]: {e}")
            return ""
        if review.get("samples", 0) < 3:
            return ""
        return f"【历史信号复盘】{review['text']}（历史判断偏乐观/偏悲观时，请相应调整信心）"

    def _consult_skills(self, context: dict[str, Any], requested: list[str] | None = None) -> tuple[list[dict], dict, str]:
        """多策略会诊：返回（各策略观点，共识，交给决策员的文本）；未启用、非个股或出错时全部为空。"""
        cfg = (self.config.get("diagnosis") or {}).get("skill_consult") or {}
        if not cfg.get("enabled", False):
            return [], {}, ""
        try:
            from src.services.diagnosis_agents import split_sections
            from src.services.skill_consult import SkillOpinionService, consensus, consult, section_text, select_skills

            regime = getattr(context.get("regime"), "regime", "") or ""
            picked = select_skills(split_sections(context["text"]), regime, int(cfg.get("max_skills", 2)), requested)
            if not picked:
                return [], {}, ""
            try:
                weights = SkillOpinionService(self.config).weights()
            except Exception as e:
                logger.debug(f"读取策略权重失败: {e}")
                weights = {}
            opinions = consult(self.llm, context["text"], picked, weights)
            if not opinions:
                return [], {}, ""
            cons = consensus(opinions)
            return opinions, cons, section_text(opinions, cons)
        except Exception as e:
            logger.warning(f"策略会诊失败 [{context.get('code')}]: {e}")
            return [], {}, ""

    def _record_opinions(self, result: dict[str, Any], diagnosis_id: int | None) -> None:
        """策略观点落库（用于后验命中率与权重）；异常只记日志。"""
        if not result.get("skill_opinions"):
            return
        try:
            from src.services.skill_consult import SkillOpinionService

            SkillOpinionService(self.config).record(
                diagnosis_id, result["code"], result.get("name", ""), result.get("trade_date", ""), result["skill_opinions"])
        except Exception as e:
            logger.warning(f"保存策略观点失败 [{result.get('code')}]: {e}")

    def latest(self, code: str, max_age_minutes: int | None = None) -> dict[str, Any] | None:
        with get_db_session(self.db_path) as session:
            query = session.query(StockDiagnosis).filter(StockDiagnosis.code == bare_code(code))
            if max_age_minutes is not None:
                query = query.filter(StockDiagnosis.created_at >= datetime.now() - timedelta(minutes=max_age_minutes))
            row = query.order_by(StockDiagnosis.created_at.desc()).first()
            return json.loads(row.result_json) if row else None

    def _calibration(self) -> dict[str, Any]:
        try:
            from src.services.diagnosis_outcome import calibration_stats

            return calibration_stats(self.config)
        except Exception as e:
            logger.debug(f"读取诊断历史表现失败: {e}")
            return {}

    @staticmethod
    def _calibration_line(calibration: dict[str, Any], code: str) -> str:
        if not calibration:
            return ""
        from src.services.diagnosis_outcome import calibration_text

        return calibration_text(calibration, code)

    def history(self, code: str, limit: int = 5) -> list[dict[str, Any]]:
        """最近几次诊断结果（新的在前）。"""
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(StockDiagnosis.result_json).filter(StockDiagnosis.code == bare_code(code))
                .order_by(StockDiagnosis.created_at.desc(), StockDiagnosis.id.desc()).limit(limit).all()
            )
        return [json.loads(r) for (r,) in rows]

    # ---------- 上下文 ----------

    def _web_search_lines(self, code: str, name: str, existing: list[str]) -> list[str]:
        """联网搜索的个股新闻（未启用搜索时不调用）；与已有资讯按标题去重，最多 5 条。"""
        from src.collectors import news_search

        if not news_search.is_enabled(self.config):
            return []
        try:
            results = news_search.search_stock_news(code, name, self.config, limit=5)
        except Exception as e:
            logger.debug(f"联网搜索个股新闻失败: {e}")
            return []
        lines = []
        for r in results:
            if any(r.title in line for line in existing):
                continue
            label = r.source or news_search.PROVIDERS.get(r.provider, r.provider)
            lines.append(f"{r.published or '近期'} [联网·{label}] {r.title}")
        return lines[:5]

    def build_context(self, code: str, run_log: RunLog | None = None) -> dict[str, Any]:
        """汇总诊断所需数据，返回 {"name", "quote", "text", ...}；text 为交给 LLM 的完整上下文。"""
        from src.analyzers.market_regime import MarketRegimeAnalyzer
        from src.analyzers.theme_tracker import ThemeTracker
        from src.strategy.tech_score import analyze_technical

        from src.collectors.daily_history import ensure_daily_history
        from src.collectors.fund_flow import describe as describe_flow
        from src.collectors.fund_flow import latest_fund_flow
        from src.collectors.fundamentals import EarningsCache, describe_chips, describe_earnings, earnings_risk, fetch_chip_summary
        from src.collectors.stock_news import get_stock_news

        step = _step_factory(run_log)
        try:  # 本地日线不足时先联网补齐，技术面和筹码估算都依赖它
            with step("补齐日线"):
                ensure_daily_history(code, self.db_path)
        except Exception as e:
            logger.debug(f"补齐日线失败 [{code}]: {e}")
        cands = code_candidates(code)
        with get_db_session(self.db_path) as session:
            with step("行情与日线") as s:
                bar_count = session.query(StockDaily.id).filter(StockDaily.code.in_(cands)).count()
                bars = (
                    session.query(StockDaily).filter(StockDaily.code.in_(cands))
                    .order_by(StockDaily.trade_date.desc()).limit(10).all()
                )
                s.detail = f"本地日线 {bar_count} 根"
            with step("资金流") as s:
                flow = latest_fund_flow(session, code)
                flow_text, flow_ratio = describe_flow(flow), (flow.net_ratio if flow else None)
                s.detail = flow_text or "暂无"
            quote = {}
            if bars:
                b = bars[0]
                quote = {
                    "trade_date": b.trade_date, "close": b.close, "change_pct": b.change_pct, "turnover": b.turnover,
                    "amount_yi": round((b.amount or 0) / 1e8, 2), "circ_mv_yi": round((b.circ_mv or 0) / 1e8, 1),
                    "pe": next((x.pe for x in bars if x.pe), None), "pb": next((x.pb for x in bars if x.pb), None),
                }
            name = normalize_name(next((b.name for b in bars if b.name), ""))
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
            name = name or normalize_name(next((r.name for r in limit_ups if r.name), ""))

            since = datetime.now() - timedelta(days=NEWS_DAYS)
            news_lines = []
            if name:
                news = (
                    session.query(FinanceNews.title, FinanceNews.source, FinanceNews.collected_at)
                    .filter(FinanceNews.collected_at >= since, or_(*[FinanceNews.title.contains(v) for v in (name_variants(name) or [name])]))
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

        with step("技术面") as s:
            tech = analyze_technical(code, self.db_path)
            s.detail = tech.brief() or "数据不足"
        tracker = ThemeTracker(self.config)
        with step("主线") as s:
            themes = tracker.analyze_all()
            role = tracker.stock_roles(themes).get(code)
            theme = next((t for t in themes if role and (t.dimension, t.name) == (role["dimension"], role["theme"])), None)
            s.detail = f"{role['role']}（{role['theme']}）" if role else "不在近期涨停主线中"
        with step("大盘环境") as s:
            regime = MarketRegimeAnalyzer(self.config).analyze()
            s.detail = regime.regime
        position = self._position(code)
        real_position = self._real_position(code)
        with step("筹码") as s:
            chip = fetch_chip_summary(code, self.db_path)
            s.detail = "已获取" if chip is not None else "暂无"
        try:
            with step("业绩") as s:
                earnings = EarningsCache.get(code)
                s.detail = "已获取" if earnings is not None else "暂无"
        except Exception as e:
            logger.debug(f"业绩数据获取失败: {e}")
            earnings = None
        try:
            with step("个股新闻公告") as s:
                stock_news = get_stock_news(code)
                s.detail = f"新闻 {len(stock_news['news'])} 条，公告 {len(stock_news['notices'])} 条"
        except Exception as e:
            logger.debug(f"个股新闻/公告获取失败: {e}")
            stock_news = {"news": [], "notices": []}
        seen = set(news_lines)
        news_lines += [f"{n['date'][:10]} [{n['source'] or '东方财富'}] {n['title']}" for n in stock_news["news"][:STOCK_NEWS_LIMIT]
                       if n["title"] not in seen]
        from src.collectors import news_search

        if news_search.is_enabled(self.config):  # 未启用联网搜索时不记录这一步
            with step("联网搜索") as s:
                web_lines = self._web_search_lines(code, name, news_lines)
                s.detail = f"{len(web_lines)} 条"
        else:
            web_lines = []
        news_lines += web_lines
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

        phase_ctx = market_phase.current_phase()
        sections = [
            f"股票：{name}({code}) {board_of(code)}",
            market_phase.phase_prompt_section(phase_ctx, (quote or {}).get("trade_date", "")),
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
            "【持仓】" + ("；".join(
                f"{label}持有 {p['quantity']} 股，成本 {p['avg_cost']:.2f}，止损 {p['stop_loss']:.2f}，目标 {p['target_price']:.2f}"
                for label, p in (("模拟盘", position), ("实盘", real_position)) if p
            ) or "未持仓"),
            f"【数据完整度】{data_quality['score']}%" + (f"（缺少：{'、'.join(data_quality['missing'])}）" if data_quality["missing"] else ""),
        ]
        review_section = self._signal_review_section(code)
        if review_section:
            sections.append(review_section)
        return {
            "code": code, "name": name, "quote": quote, "tech": tech, "role": role, "regime": regime,
            "position": position, "real_position": real_position, "text": "\n".join(sections), "data_quality": data_quality,
            "flow_text": flow_text, "flow_ratio": flow_ratio, "chip": chip,
            "earnings_text": describe_earnings(earnings), "earnings_risk": earnings_risk(earnings),
            "risk_notices": risk_notices,
            "valuation_text": valuation_text(quote.get("pe"), quote.get("pb")) if quote else "",
            "phase": phase_ctx,
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
        valuation = valuation_text(quote.get("pe"), quote.get("pb"))
        if valuation:
            parts.append(valuation)
        return "，".join(parts)

    def _real_position(self, code: str) -> dict[str, Any] | None:
        try:
            from src.services.real_portfolio import RealPortfolioService

            return next((p for p in RealPortfolioService(self.config).positions() if p["code"] == code), None)
        except Exception as e:
            logger.debug(f"读取实盘持仓失败: {e}")
            return None

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
        if context.get("disagreement") and confidence == "高":
            guardrails.append(f"分析员观点分歧（{context['disagreement']}），信心下调为中")
            confidence = "中"
        bullish_history = (context.get("calibration") or {}).get("看多") or {}
        if (action in BULLISH_ACTIONS and bullish_history.get("n", 0) >= MIN_CALIBRATION_SAMPLES
                and bullish_history.get("accuracy") is not None and bullish_history["accuracy"] < LOW_CALIBRATION_ACCURACY):
            lowered = {"高": "中", "中": "低"}.get(confidence, "低")
            guardrails.append(f"近 90 天看多诊断 3 日准确率仅 {bullish_history['accuracy']}%（{bullish_history['n']} 次），信心下调为{lowered}")
            confidence = lowered
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

        phase_ctx = context.get("phase")
        phase_decision: dict = {}
        if phase_ctx:
            action, confidence, phase_decision, phase_notes = market_phase.phase_guardrails(
                action, confidence, raw.get("phase_decision"), phase_ctx, (context["quote"] or {}).get("trade_date", ""))
            guardrails.extend(phase_notes)

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
            "valuation": context.get("valuation_text", ""),
            "agents": context.get("opinions") or [],
            "disagreement": context.get("disagreement", ""),
            "calibration": self._calibration_line(context.get("calibration") or {}, context["code"]),
            "invalidation": str(raw.get("invalidation") or "")[:MAX_INVALIDATION_LEN].strip(),
            "horizon_days": _horizon(raw.get("horizon_days")),
            "phase_decision": phase_decision,
            "signal_attribution": normalize_attribution(raw.get("signal_attribution")),
            "market_phase": {k: phase_ctx.get(k) for k in ("phase", "label", "now", "effective_daily_bar_date")} if phase_ctx else {},
        }

    def _save(self, result: dict[str, Any]) -> int | None:
        """保存诊断，返回记录 id。"""
        with get_db_session(self.db_path) as session:
            row = StockDiagnosis(
                code=result["code"], name=result["name"], trade_date=result["trade_date"], action=result["action"],
                score=result["score"], result_json=json.dumps(result, ensure_ascii=False),
                run_log=json.dumps(result["run_log"], ensure_ascii=False) if result.get("run_log") else None,
            )
            session.add(row)
            session.flush()
            return row.id


ATTRIBUTION_LABELS = (("technical", "技术面"), ("news", "资讯"), ("fundamentals", "基本面"), ("market", "大盘"))


def _phase_markdown(result: dict[str, Any]) -> list[str]:
    """阶段决策和信号归因两节，字段为空时不输出。"""
    lines: list[str] = []
    pd = result.get("phase_decision") or {}
    body = []
    if pd.get("phase_label"):
        body.append(f"- 阶段：{pd['phase_label']}")
    for key, label in (("trading_window", "操作窗口"), ("immediate_action", "立即行动")):
        if pd.get(key):
            body.append(f"- {label}：{pd[key]}")
    if pd.get("watch_conditions"):
        body.append("- 观察条件：")
        body += [f"  - {c}" for c in pd["watch_conditions"]]
    if pd.get("next_check_time"):
        body.append(f"- 下次检查：{pd['next_check_time']}")
    if pd.get("data_limitations"):
        body.append("- 数据限制：" + "；".join(pd["data_limitations"]))
    if len(body) > 1 or (body and not pd.get("phase_label")):
        lines += ["### 阶段决策", "\n".join(body)]
    attr = result.get("signal_attribution") or {}
    parts = [f"{label} {attr[key]}%" for key, label in ATTRIBUTION_LABELS if attr.get(key) is not None]
    abody = []
    if parts:
        abody.append("- 贡献度：" + "，".join(parts))
    if attr.get("strongest_bullish"):
        abody.append(f"- 最强看多：{attr['strongest_bullish']}")
    if attr.get("strongest_bearish"):
        abody.append(f"- 最强看空：{attr['strongest_bearish']}")
    if abody:
        lines += ["### 信号归因", "\n".join(abody)]
    return lines


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
    lines += _phase_markdown(result)
    if result["theme_role"]:
        role = result["theme_role"]
        lines.append(f"**主线地位**：{role['theme']}（{role['phase']}）{role['role']}")
    lines.append(f"**大盘**：{result['market_regime']}")
    from src.collectors.fundamentals import describe_chips

    for label, text in (("资金", result.get("fund_flow")), ("筹码", describe_chips(result.get("chips"))),
                        ("业绩", result.get("earnings")), ("估值", result.get("valuation"))):
        if text:
            lines.append(f"**{label}**：{text}")
    agents = [a for a in result.get("agents") or [] if not a.get("error")]
    if agents:
        lines += ["### 分析员观点", *[f"- {a['label']}：{a['view']} {a['score']}分（信心{a['confidence']}）"
                                    + (f"；{'；'.join(a['key_points'])}" if a["key_points"] else "") for a in agents]]
        lines.append(f"**分歧**：{result['disagreement']}" if result.get("disagreement") else "**分歧**：观点基本一致")
    if result.get("calibration"):
        lines.append(result["calibration"].replace("【历史表现】", "**历史表现**："))
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
