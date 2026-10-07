"""
ETF / 指数 AI 诊断（对齐个股诊断的决策仪表盘，结果字段一致并额外带 kind）

行情来自 fund_daily（不写 stock_daily），本地日线不足时联网补齐并保持最新。
上下文：最近走势、技术面（均线、MACD、RSI、量能）、大盘环境、主线（名称里的行业/题材关键词与主线匹配）、
资讯（联网搜索、财联社标题含名称的近 7 天新闻）。资金流、筹码、业绩、公告、龙虎榜、持仓等个股专属项不适用，
数据完整度只按适用项计权。护栏复用个股诊断：评分低于 50、大盘冰点、数据不足时买入降为观望。
结果写入 stock_diagnosis 表（code 为规范代码），30 分钟内复用。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from src.database.db import get_db_session
from src.database.models import FinanceNews, FundDaily, StockDiagnosis
from src.services import market_phase
from src.services.report_language import language_directive, report_language
from src.services.run_log import RunLog, run_scope
from src.utils.stock_code import diagnosis_code
from src.utils.redaction import redact
from src.services.execution_budget import ExecutionBudget
from src.services.diagnosis_prompts import (
    DECISION_ADDENDUM_EN, FUND_SYSTEM_PROMPT_EN, context_text, english_context, news_facts, phase_facts,
)
from src.services.decision_profile import normalize_profile
from src.services.stock_diagnosis import (
    CACHE_MINUTES,
    DIAGNOSIS_ENUMS,
    MIN_DAILY_BARS,
    STABILITY_DAYS,
    StockDiagnosisService,
    _step_factory,
    news_section,
    note_guardrail_change,
)

FUND_NEWS_DAYS = 7
RECENT_BARS = 10
TECH_BARS = 120
# 数据完整度各块权重（合计 100）：个股专属的资金流、筹码、业绩不参与
FUND_QUALITY_WEIGHTS = {"行情": 25, "日线": 20, "技术面": 20, "大盘": 20, "资讯": 15}
KIND_LABELS = {"etf": "ETF", "index": "指数"}
# 名称里的行业/题材关键词 → 主线名称里可能出现的写法
THEME_KEYWORDS = (
    "半导体", "芯片", "电子", "计算机", "软件", "人工智能", "通信", "传媒", "游戏", "互联网",
    "新能源", "光伏", "锂电", "电池", "储能", "风电", "汽车", "机器人", "军工", "航天", "航空",
    "医药", "医疗", "创新药", "生物", "中药", "证券", "券商", "银行", "保险", "金融", "地产", "房地产",
    "煤炭", "钢铁", "有色", "稀土", "黄金", "化工", "石油", "电力", "家电", "白酒", "酒", "食品", "消费",
    "农业", "养殖", "环保", "科技", "红利", "红利低波",
)

FUND_SYSTEM_PROMPT = """你是一位 A 股 ETF / 指数分析师，负责对单个 ETF 或宽基/行业指数生成【决策仪表盘】。

分析原则：
- 先看【市场阶段】：盘前/非交易日给开盘计划，盘中给当下可执行动作和观察条件，盘后按完整交易日复盘
- 先看大盘环境，再看该标的自身的趋势、均线、MACD、RSI 和量能，最后看所属行业/题材是否处于市场主线
- ETF 和指数没有涨跌停、没有个股财报和公告风险，重点是趋势、位置和资金/量能配合；指数本身不能直接买入，
  只能理解为对应 ETF 或指数基金的方向判断
- 结论必须落到具体操作（买/不买、仓位、止损），只使用给出的数据，没有的数据不要编造
- 【对应主线】写「无对应主线」只说明近期涨停主线里没有对应板块，不代表利空

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
  "checklist": [{"item": "检查项（如趋势/均线/量能/大盘环境/主线/消息面）", "status": "pass/warn/fail", "note": "说明"}],
  "invalidation": "失效条件：出现什么情况说明判断错了（如跌破某价、放量滞涨，40字以内）",
  "horizon_days": 观察期，1到20的整数（交易日数，通常 3-5）,
  "phase_decision": {"trading_window": "当前阶段的操作窗口（如开盘后 30 分钟观察承接）", "immediate_action": "现在立刻做什么（盘前/非交易日不能是立即买卖）", "watch_conditions": ["触发条件1", "触发条件2"], "next_check_time": "下次检查时间（如 10:00、下一交易日 9:25）"},
  "signal_attribution": {"technical": 技术面贡献0-100, "news": 资讯情绪贡献0-100, "fundamentals": 基本面贡献0-100, "market": 大盘环境贡献0-100, "strongest_bullish": "最强看多信号", "strongest_bearish": "最强看空信号"},
  "analysis": "综合分析（100字以内）"
}"""


class FundDiagnosisService(StockDiagnosisService):
    """ETF / 指数的 AI 诊断；护栏、落库和 Markdown 渲染复用个股诊断。"""

    @run_scope
    def diagnose(self, code: str, force: bool = False, profile: str | None = None) -> dict[str, Any]:
        """诊断一个 ETF/指数（传规范代码或任意可识别的写法）；30 分钟内的结果直接复用。"""
        from src.services.diagnosis_agents import DECISION_ADDENDUM, disagreement, opinions_text, run_analysts
        from src.services.fund_registry import resolve_fund

        info = resolve_fund(code, self.db_path)
        if not info:
            return {"code": code, "error": "不是已知的 ETF 或指数"}
        code = info["code"]
        lang = report_language(self.config)
        profile = self._profile(profile)
        if not force:
            cached = self.latest(code, max_age_minutes=CACHE_MINUTES)
            if cached and (cached.get("language") or "zh") == lang and normalize_profile(cached.get("decision_profile")) == profile:
                return {**cached, "cached": True}

        cfg = self.config.get("diagnosis") or {}
        if self._llm is None and not getattr(self, "_isolated_worker", False) and cfg.get("isolate_process", False):
            from src.services.analysis_process import isolated_result
            return isolated_result("fund_diagnosis", self.config, {"code": code, "force": force, "profile": profile})
        budget = ExecutionBudget.from_config(self.config)

        run_log = RunLog()
        context = self.build_context(code, info, run_log)
        from src.services.research_artifact import build_context_pack
        context["context_pack"] = build_context_pack(context, run_log)
        budget.check("取数")
        if not context["quote"]:
            return {"code": code, "name": context["name"], "kind": info["kind"], "error": "行情库中没有该基金/指数的数据"}
        cfg = self.config.get("diagnosis") or {}
        model = str((getattr(self.llm, "primary_cfg", None) or {}).get("model") or "")
        analyst_start = time.perf_counter()
        message = context_text(context, lang)
        opinions = run_analysts(self.llm, message, str(cfg.get("mode", "single")), lang, budget=budget)
        if opinions:
            run_log.llm("分析员", model, True, (time.perf_counter() - analyst_start) * 1000)
        conflict = disagreement(opinions, lang)
        if opinions:
            message += "\n" + opinions_text(opinions, conflict, lang)
        decision_start = time.perf_counter()
        budget.check("决策")
        prompt = FUND_SYSTEM_PROMPT_EN if lang == "en" else FUND_SYSTEM_PROMPT
        addendum = DECISION_ADDENDUM_EN if lang == "en" else DECISION_ADDENDUM
        raw = self.llm.chat_json(user_message=message, system_message=prompt + (addendum if opinions else "")
                                  + language_directive(lang, DIAGNOSIS_ENUMS))
        run_log.llm("决策", model, bool(raw), (time.perf_counter() - decision_start) * 1000)
        budget.check("决策")
        if not raw:
            return {"code": code, "name": context["name"], "kind": info["kind"], "error": "AI 未返回有效结果，请检查 AI 设置或稍后重试"}
        previous = self.latest(code, max_age_minutes=STABILITY_DAYS * 24 * 60)
        context = {**context, "opinions": opinions, "disagreement": conflict, "calibration": {}, "profile": profile}
        result = self._apply_guardrails(raw, context, previous)
        result["kind"] = info["kind"]
        note_guardrail_change(run_log, raw, result)
        result["run_log"] = run_log.to_dict()
        from src.services.research_artifact import build_research_artifact
        result["structured_report"] = build_research_artifact(result)
        diagnosis_id = self._save(result)
        self._record_signal(result, diagnosis_id)
        result["diagnosis_id"] = diagnosis_id
        return result

    def latest(self, code: str, max_age_minutes: int | None = None) -> dict[str, Any] | None:
        with get_db_session(self.db_path) as session:
            query = session.query(StockDiagnosis).filter(StockDiagnosis.code == diagnosis_code(code))
            if max_age_minutes is not None:
                query = query.filter(StockDiagnosis.created_at >= datetime.now() - timedelta(minutes=max_age_minutes))
            row = query.order_by(StockDiagnosis.created_at.desc(), StockDiagnosis.id.desc()).first()
            return redact({**json.loads(row.result_json), "diagnosis_id": row.id}) if row else None

    def history(self, code: str, limit: int = 5) -> list[dict[str, Any]]:
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(StockDiagnosis.result_json).filter(StockDiagnosis.code == diagnosis_code(code))
                .order_by(StockDiagnosis.created_at.desc(), StockDiagnosis.id.desc()).limit(limit).all()
            )
        return [redact(json.loads(r)) for (r,) in rows]

    # ---------- 上下文 ----------

    def _load_bars(self, code: str) -> list[FundDaily]:
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(FundDaily).filter(FundDaily.code == code)
                .order_by(FundDaily.trade_date.desc()).limit(TECH_BARS).all()
            )
            session.expunge_all()
        return list(reversed(rows))

    @staticmethod
    def match_theme(name: str, themes: list) -> Any | None:
        """名称里的行业/题材关键词与主线板块名称匹配，取热度最高的一个；没有返回 None。"""
        keys = [k for k in THEME_KEYWORDS if k in name]
        if not keys:
            return None
        hits = [t for t in themes if any(k in t.name or t.name in name for k in keys)]
        return max(hits, key=lambda t: t.heat) if hits else None

    def build_context(self, code: str, info: dict[str, Any] | None = None,
                      run_log: RunLog | None = None) -> dict[str, Any]:  # type: ignore[override]
        """汇总诊断所需数据，返回 {"name", "quote", "text", ...}；text 为交给 LLM 的完整上下文。"""
        from src.analyzers.market_regime import MarketRegimeAnalyzer
        from src.analyzers.theme_tracker import ThemeTracker
        from src.collectors import fund_data
        from src.services.fund_registry import resolve_fund
        from src.strategy.tech_score import analyze_series

        info = info or resolve_fund(code, self.db_path) or {"kind": "etf", "code": code, "name": ""}
        kind = info["kind"]
        step = _step_factory(run_log)
        try:  # 本地日线不足时联网补齐，再把落后的最新几根补上
            with step("补齐日线"):
                fund_data.ensure_fund_daily(code, self.db_path)
                fund_data.refresh_recent_fund_daily(code, self.db_path)
        except Exception as e:
            logger.debug(f"补齐 ETF/指数日线失败 [{code}]: {e}")
        with step("行情与日线") as s:
            bars = self._load_bars(code)
            s.detail = f"本地日线 {len(bars)} 根"
        name = next((b.name for b in reversed(bars) if b.name), "") or info.get("name") or ""
        quote: dict[str, Any] = {}
        if bars:
            last = bars[-1]
            quote = {
                "trade_date": last.trade_date, "close": last.close, "change_pct": last.change_pct,
                "amount_yi": round((last.amount or 0) / 1e8, 2),
                "source": last.source, "price_adjustment": last.price_adjustment,
                "updated_at": last.updated_at.isoformat() if last.updated_at else None,
            }
        recent = [f"{b.trade_date} 收{b.close} {b.change_pct:+.2f}%" for b in bars[-RECENT_BARS:] if b.close and b.change_pct is not None]
        closes = [b.close for b in bars if b.close]
        with step("技术面") as s:
            tech = analyze_series(closes, [b.volume for b in bars if b.volume], [b.change_pct for b in bars if b.change_pct is not None])
            s.detail = tech.brief() or "数据不足"
        tech_text = self._technical_text(bars, tech)

        tracker = ThemeTracker(self.config)
        try:
            with step("主线"):
                themes = tracker.analyze_all()
        except Exception as e:
            logger.debug(f"读取主线失败: {e}")
            themes = []
        theme = self.match_theme(name, themes)
        with step("大盘环境") as s:
            regime = MarketRegimeAnalyzer(self.config).analyze()
            s.detail = regime.regime

        since = datetime.now() - timedelta(days=FUND_NEWS_DAYS)
        news_lines: list[str] = []
        if name:
            with get_db_session(self.db_path) as session:
                rows = (
                    session.query(FinanceNews.title, FinanceNews.source)
                    .filter(FinanceNews.collected_at >= since, FinanceNews.title.contains(name))
                    .order_by(FinanceNews.collected_at.desc()).limit(8).all()
                )
            news_lines = [f"[{src}] {title}" for title, src in rows]
        web_lines, web_status, _ = self._web_search("" if kind == "index" else code, name, news_lines)
        news_lines += web_lines

        present = {
            "行情": bool(quote), "日线": len(closes) >= MIN_DAILY_BARS, "技术面": bool(tech.brief()),
            "大盘": regime.regime != "未知", "资讯": bool(news_lines),
        }
        data_quality = {
            "score": sum(FUND_QUALITY_WEIGHTS[k] for k, ok in present.items() if ok),
            "missing": [k for k, ok in present.items() if not ok],
            "core_ok": present["行情"] and present["日线"],
            "bar_count": len(closes),
        }
        theme_text = f"对应主线：{theme.brief()}" if theme else "无对应主线"
        phase_ctx = market_phase.current_phase()
        sections = [
            f"标的：{name}({code}) {KIND_LABELS[kind]}",
            market_phase.phase_prompt_section(phase_ctx, (quote or {}).get("trade_date", "")),
            "【行情】" + (f"{quote['trade_date']} 收盘 {quote['close']}（{quote['change_pct']:+.2f}%）"
                        + (f"，成交约 {quote['amount_yi']} 亿" if quote.get("amount_yi") else "") if quote else "暂无"),
            "【近期走势】" + ("；".join(recent) if recent else "暂无"),
            f"【技术面】{tech_text or '数据不足'}",
            f"【对应主线】{theme_text}",
            f"【大盘环境】{regime.summary()}",
            news_section(news_lines, web_status),
            f"【数据完整度】{data_quality['score']}%" + (f"（缺少：{'、'.join(data_quality['missing'])}）" if data_quality["missing"] else ""),
            f"说明：这是{KIND_LABELS[kind]}，没有涨跌停、资金流、筹码、业绩和公告数据，请只基于以上数据判断。",
        ]
        review_section = self._signal_review_section(code)
        if review_section:
            sections.append(review_section)
        text_en = english_context({
            "股票": {"name": name, "code": code, "kind": kind}, "市场阶段": phase_facts(phase_ctx, quote.get("trade_date", "")), "行情": quote,
            "近期走势": [{"trade_date": b.trade_date, "close": b.close, "volume": b.volume,
                          "change_pct": b.change_pct} for b in bars[-RECENT_BARS:]],
            "技术面": {"indicators": tech, "source_evidence": tech_text}, "对应主线": theme,
            "大盘环境": regime, "相关资讯": news_facts(news_lines, web_status), "数据完整度": data_quality,
            "Applicability": "Individual-stock fund flows, chips, earnings, announcements and holdings are not supplied. An index cannot be traded directly. No theme match is not bearish evidence.",
            **({"Historical signal review": self._signal_review_section(code, "en")} if review_section else {}),
        }) if report_language(self.config) == "en" else ""
        return {
            "code": code, "name": name, "kind": kind, "quote": quote, "tech": tech, "role": {}, "regime": regime,
            "position": None, "real_position": None, "text": "\n".join(sections), "text_en": text_en, "data_quality": data_quality,
            "flow_text": "", "flow_ratio": None, "chip": None, "earnings_text": "", "earnings_risk": "",
            "risk_notices": [], "valuation_text": "",
            "news_evidence": news_lines, "web_status": web_status,
            "phase": phase_ctx,
        }

    @staticmethod
    def _technical_text(bars: list[FundDaily], tech) -> str:
        """技术面一句话：评分、均线位置、MACD、RSI 和量能。"""
        from src.analyzers.indicators import sma

        closes = [b.close for b in bars if b.close]
        if len(closes) < MIN_DAILY_BARS:
            return ""
        parts = [f"技术评分 {tech.score:.0f}", tech.brief()]
        ma = {n: sma(closes, n) for n in (5, 10, 20, 60)}
        parts.append("均线 " + " ".join(f"MA{n}={v:.3f}" for n, v in ma.items() if v is not None))
        volumes = [b.volume for b in bars if b.volume]
        if len(volumes) >= 20:
            parts.append(f"5日均量/20日均量={sum(volumes[-5:]) / 5 / (sum(volumes[-20:]) / 20):.2f}")
        if tech.reasons:
            parts.append("利好信号：" + "、".join(tech.reasons))
        return "；".join(p for p in parts if p)

    def _apply_guardrails(self, raw: dict, context: dict, previous: dict | None = None) -> dict[str, Any]:
        result = super()._apply_guardrails(raw, context, previous)
        result["kind"] = context.get("kind", "")
        return result
