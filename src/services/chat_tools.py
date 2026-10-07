"""
AI 问股的工具集：只读本地数据库和已有的采集接口，返回给 LLM 的简短文本；不下单、不改数据。

股票参数可以是代码（600519、sh600519）或名称/拼音首字母，会先经股票搜索解析成代码。
ETF 和指数（代码 510300、sh000300，或名称沪深300）从 fund_daily 取数，只支持 resolve_stock、quote、daily_bars、
technical、diagnosis；资金流、筹码、业绩、新闻公告等个股专属工具遇到它们返回「该工具只适用于个股」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from loguru import logger

from src.database.db import get_db_session
from src.database.models import LimitUpStock, StockDaily
from src.utils.stock_code import bare_code, board_of, code_candidates
from src.utils.redaction import redact_text

MAX_RESULT_CHARS = 1500
DEFAULT_BAR_DAYS = 20
MAX_BAR_DAYS = 60
STOCK_ONLY_TEXT = "该工具只适用于个股，不支持 ETF 和指数"
KIND_LABELS = {"etf": "ETF", "index": "指数"}


class StockOnlyError(ValueError):
    """个股专属工具收到了 ETF/指数。"""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str           # 界面上显示的中文名
    args: str            # 给 LLM 的参数说明
    description: str


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("context_pack", "统一研究证据", "code", "本地行情、日线、财务和新闻证据包，明确缺失和来源；不会联网"),
    ToolSpec("resolve_stock", "查找股票", "query: 名称/代码/拼音首字母", "按名称、代码或拼音首字母查找股票代码"),
    ToolSpec("quote", "最新行情", "code", "最新收盘价、涨跌幅、成交额、换手率、流通市值、市盈率、市净率"),
    ToolSpec("daily_bars", "日线走势", f"code, days（默认 {DEFAULT_BAR_DAYS}，最多 {MAX_BAR_DAYS}）", "最近 N 个交易日的收盘价和涨跌幅"),
    ToolSpec("technical", "技术面", "code", "均线趋势、MACD、RSI、乖离率、量比、技术信号和风险"),
    ToolSpec("fund_flow", "资金流", "code", "最近一个交易日的主力资金净流入"),
    ToolSpec("chips", "筹码", "code", "获利盘比例、平均成本、90% 筹码区间和集中度"),
    ToolSpec("earnings", "业绩", "code", "最新业绩预告或业绩快报"),
    ToolSpec("shareholders", "股东", "code", "股东户数及变化、十大流通股东明细、机构持仓"),
    ToolSpec("news", "新闻公告", "code", "近 7 天个股新闻和近 30 天公告（标注立案、减持等风险）"),
    ToolSpec("web_search", "联网搜索", "query: 搜索词", "搜索最新新闻，需要在设置里配置搜索服务"),
    ToolSpec("limit_up_history", "涨停记录", "code", "近期涨停记录：连板、封板时间、炸板次数、涨停原因"),
    ToolSpec("theme", "主线地位", "code（可选）", "当前题材/行业主线；给 code 时说明该股是否为主线龙头或跟风"),
    ToolSpec("market", "大盘环境", "无", "指数、涨跌家数、成交额、量化大盘环境、主线和降温板块、重要快讯"),
    ToolSpec("screening", "策略选股", "code（可选）", "最近一次全市场策略选股结果；给 code 时说明该股是否入选及理由"),
    ToolSpec("position", "持仓", "code（可选）", "模拟盘和实盘记账的持仓、成本、止损价和目标价"),
    ToolSpec("diagnosis", "历史诊断", "code", "该股最近一次 AI 诊断的结论和评分"),
    ToolSpec("watchlist", "自选股", "无", "自选股列表、最新涨跌和每只最近一次 AI 诊断结论"),
)
TOOL_LABELS = {t.name: t.label for t in TOOL_SPECS}


def tools_prompt() -> str:
    return "\n".join(f"- {t.name}({t.args})：{t.description}" for t in TOOL_SPECS)


def native_tool_schemas() -> list[dict]:
    tools = []
    for spec in TOOL_SPECS:
        properties = {}
        for name in ("code", "query", "days"):
            if name in spec.args:
                properties[name] = {"type": "integer" if name == "days" else "string"}
        required = [name for name in properties if name != "days" and "可选" not in spec.args]
        tools.append({"type": "function", "function": {"name": spec.name, "description": spec.description,
            "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}})
    return tools


class ChatTools:
    def _tool_context_pack(self, args: dict) -> str:
        import json
        from src.services.research_artifact import local_context_pack
        code = self._resolve_kind(args)[0]
        self.last_context_pack = local_context_pack(code, self.db_path)
        return json.dumps(self.last_context_pack, ensure_ascii=False)

    def __init__(self, config: dict):
        self.config = config
        self.db_path = config.get("database", {}).get("sqlite_path", "data/quant.db")
        self._handlers: dict[str, Callable[[dict], str]] = {t.name: getattr(self, f"_tool_{t.name}") for t in TOOL_SPECS}

    def call(self, name: str, args: dict[str, Any] | None) -> str:
        """执行一个工具，异常和未知工具都返回说明文字（交给 LLM 自己处理）。"""
        handler = self._handlers.get(name)
        if handler is None:
            return f"没有名为 {name} 的工具，可用：{'、'.join(self._handlers)}"
        try:
            text = handler(args if isinstance(args, dict) else {})
        except StockOnlyError:
            return STOCK_ONLY_TEXT
        except Exception as e:
            logger.warning(f"问股工具 {name} 失败: {e}")
            return f"工具执行失败：{redact_text(e, 300)}"
        return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + "…（已截断）"

    # ---- 股票解析 ----

    def _resolve_kind(self, args: dict, required: bool = True) -> tuple[str, str, str]:
        """(代码, 名称, 类型 stock/etf/index)；参数为名称/拼音时先搜索。ETF/指数返回规范代码。"""
        raw = str(args.get("code") or args.get("query") or args.get("name") or "").strip()
        if not raw:
            if required:
                raise ValueError("缺少股票代码")
            return "", "", "stock"
        from src.services.fund_registry import resolve_fund

        fund = resolve_fund(raw, self.db_path)
        if fund:
            return fund["code"], fund["name"], fund["kind"]
        bare = bare_code(raw)
        if not (len(bare) == 6 and bare.isdigit()):
            from src.services.stock_search import StockSearch

            found = StockSearch(self.db_path).search(raw, 1)
            if not found:
                raise ValueError(f"找不到股票「{raw}」")
            return found[0]["code"], found[0]["name"], found[0].get("kind", "stock")
        with get_db_session(self.db_path) as session:
            name = (
                session.query(StockDaily.name).filter(StockDaily.code.in_(code_candidates(bare)), StockDaily.name.isnot(None))
                .order_by(StockDaily.trade_date.desc()).limit(1).scalar()
            )
        return bare, name or "", "stock"

    def _resolve(self, args: dict, required: bool = True) -> tuple[str, str]:
        """(代码, 名称)，只接受个股；ETF/指数抛出 StockOnlyError（由 call() 转成说明文字）。"""
        code, name, kind = self._resolve_kind(args, required)
        if kind != "stock":
            raise StockOnlyError(STOCK_ONLY_TEXT)
        return code, name

    def _fund_bars(self, code: str, min_bars: int = 0) -> list[dict]:
        """ETF/指数日线（升序）；本地不足时联网补齐，补齐失败不影响已有数据。"""
        from src.collectors import fund_data

        try:
            fund_data.ensure_fund_daily(code, self.db_path)
            fund_data.refresh_recent_fund_daily(code, self.db_path)
        except Exception as e:
            logger.debug(f"补齐 ETF/指数日线失败 [{code}]: {e}")
        return fund_data.get_fund_daily(code, self.db_path)

    # ---- 工具 ----

    def _tool_resolve_stock(self, args: dict) -> str:
        from src.services.stock_search import StockSearch

        found = StockSearch(self.db_path).search(str(args.get("query") or args.get("code") or ""), 5)
        return "；".join(
            f"{r['name']}({r['code']}，{r['kind']})" if r.get("kind", "stock") != "stock" else f"{r['name']}({r['code']})"
            for r in found
        ) or "没有找到匹配的股票"

    def _tool_quote(self, args: dict) -> str:
        code, name, kind = self._resolve_kind(args)
        if kind != "stock":
            bars = self._fund_bars(code)
            if not bars:
                return f"{name}({code}) 行情库中没有数据"
            b = bars[-1]
            parts = [f"{b['name'] or name}({code}) {KIND_LABELS[kind]} {b['trade_date']} 收盘 {b['close']}（{b['change_pct'] or 0:+.2f}%）"]
            if b.get("amount"):
                parts.append(f"成交约 {b['amount'] / 1e8:.2f} 亿")
            return "，".join(parts)
        with get_db_session(self.db_path) as session:
            b = (
                session.query(StockDaily).filter(StockDaily.code.in_(code_candidates(code)))
                .order_by(StockDaily.trade_date.desc()).first()
            )
            if not b:
                return f"{name}({code}) 行情库中没有数据"
            parts = [f"{b.name or name}({code}) {board_of(code)} {b.trade_date} 收盘 {b.close}（{b.change_pct or 0:+.2f}%）"]
            if b.amount:
                parts.append(f"成交 {b.amount / 1e8:.2f} 亿")
            if b.turnover:
                parts.append(f"换手 {b.turnover:.2f}%")
            if b.circ_mv:
                parts.append(f"流通市值 {b.circ_mv / 1e8:.0f} 亿")
            from src.services.stock_diagnosis import valuation_text

            valuation = valuation_text(b.pe, b.pb)
            if valuation:
                parts.append(valuation)
        return "，".join(parts)

    def _tool_daily_bars(self, args: dict) -> str:
        from src.collectors.daily_history import ensure_daily_history

        code, name, kind = self._resolve_kind(args)
        days = max(1, min(int(args.get("days") or DEFAULT_BAR_DAYS), MAX_BAR_DAYS))
        if kind != "stock":
            bars = [b for b in self._fund_bars(code) if b["close"]][-days:]
            if not bars:
                return f"{name}({code}) 没有日线数据"
            return f"{name}({code}) 近 {len(bars)} 日：" + "；".join(f"{b['trade_date'][5:]} {b['close']}({b['change_pct'] or 0:+.1f}%)" for b in bars)
        ensure_daily_history(code, self.db_path, name)
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(StockDaily.trade_date, StockDaily.close, StockDaily.change_pct)
                .filter(StockDaily.code.in_(code_candidates(code)), StockDaily.close > 0)
                .order_by(StockDaily.trade_date.desc()).limit(days * 2).all()
            )
        by_date = {d: (c, ch) for d, c, ch in rows}
        dates = sorted(by_date)[-days:]
        if not dates:
            return f"{name}({code}) 没有日线数据"
        return f"{name}({code}) 近 {len(dates)} 日：" + "；".join(f"{d[5:]} {by_date[d][0]}({by_date[d][1] or 0:+.1f}%)" for d in dates)

    def _tool_technical(self, args: dict) -> str:
        from src.collectors.daily_history import ensure_daily_history
        from src.strategy.tech_score import analyze_technical

        code, name, kind = self._resolve_kind(args)
        if kind != "stock":
            from src.strategy.tech_score import analyze_series

            bars = self._fund_bars(code)
            tech = analyze_series([b["close"] for b in bars if b["close"]], [b["volume"] for b in bars if b["volume"]],
                                  [b["change_pct"] for b in bars if b["change_pct"] is not None])
            text = f"{name}({code}) 技术评分 {tech.score:.0f}：{tech.brief() or '日线不足'}"
            return text + (f"；利好信号：{'、'.join(tech.reasons)}" if tech.reasons else "")
        ensure_daily_history(code, self.db_path, name)
        tech = analyze_technical(code, self.db_path)
        text = f"{name}({code}) 技术评分 {tech.score:.0f}：{tech.brief() or '日线不足'}"
        return text + (f"；利好信号：{'、'.join(tech.reasons)}" if tech.reasons else "")

    def _tool_fund_flow(self, args: dict) -> str:
        from src.collectors.fund_flow import describe, latest_fund_flow, supplement_flow

        code, name = self._resolve(args)
        with get_db_session(self.db_path) as session:
            flow = latest_fund_flow(session, code)
            text = describe(flow if flow is not None else supplement_flow(code, self.config))
        return f"{name}({code}) 资金流：{text or '暂无数据'}"

    def _tool_chips(self, args: dict) -> str:
        from src.collectors.fundamentals import describe_chips, fetch_chip_summary

        code, name = self._resolve(args)
        chip = fetch_chip_summary(code, self.db_path, self.config) if (self.config.get("data_sources") or {}).get("miaoxiang_api_key") else fetch_chip_summary(code, self.db_path)
        return f"{name}({code}) 筹码：{describe_chips(chip) or '暂无数据'}"

    def _tool_earnings(self, args: dict) -> str:
        from src.collectors.fundamentals import EarningsCache, describe_earnings, earnings_risk

        code, name = self._resolve(args)
        earnings = EarningsCache.get(code)
        risk = earnings_risk(earnings)
        return f"{name}({code}) 业绩：{describe_earnings(earnings) or '近期无业绩预告/快报'}" + (f"（风险：{risk}）" if risk else "")

    def _tool_shareholders(self, args: dict) -> str:
        from src.collectors import shareholders

        code, name = self._resolve(args)
        data = shareholders.fetch_shareholders(code)
        text = shareholders.describe_shareholders(data)
        if not text:
            return f"{name}({code}) 股东：暂无数据"
        lines = [f"{name}({code}) 股东：{text}"]
        for i, h in enumerate((data or {}).get("top10_float") or [], 1):
            ratio = f"{h['ratio']:.2f}%" if h.get("ratio") is not None else "-"
            lines.append(f"{i}. {h['name']}（{h.get('type') or '-'}）占流通股 {ratio}" + (f"，{h['change']}" if h.get("change") else ""))
        return "\n".join(lines)

    def _tool_news(self, args: dict) -> str:
        from src.collectors.stock_news import get_stock_news

        code, name = self._resolve(args)
        data = get_stock_news(code)
        notices = [f"{n['date']} {n['title']}" + (f"【风险：{n['risk']}】" if n["risk"] else "") for n in data["notices"][:8]]
        news = [f"{n['date'][:10]} {n['title']}" for n in data["news"][:8]]
        return (f"{name}({code}) 公告：" + ("；".join(notices) or "近 30 天无")
                + "\n新闻：" + ("；".join(news) or "近 7 天无"))

    def _tool_web_search(self, args: dict) -> str:
        from src.collectors import news_search

        if not news_search.is_enabled(self.config):
            return "没有配置联网搜索服务，可以用 news 工具查东方财富的个股新闻和公告"
        query = str(args.get("query") or args.get("code") or "").strip()
        if not query:
            raise ValueError("缺少搜索词")
        results = news_search.search(query, self.config, max_results=8)
        if not results:
            return f"联网搜索「{query}」没有找到结果"
        code = name = ""
        if args.get("code"):  # 只给 query 时不做相关度标注
            try:
                code, name, kind = self._resolve_kind({"code": args["code"]})
                if kind != "stock":
                    code = name = ""
            except Exception:
                code = name = ""
        tags: dict[int, str] = {}
        if code:
            from src.collectors.news_relevance import rank_news

            items = [{"title": r.title, "snippet": r.snippet, "url": r.url, "source": r.source, "_r": r} for r in results]
            ranked = rank_news(items, code, name)
            results = [it["_r"] for it in ranked]
            tags = {id(it["_r"]): f"[{it['relevance']['label']}] " for it in ranked}
            if not results:
                return f"联网搜索「{query}」没有找到与 {name}({code}) 相关的结果"
        return "\n".join(
            f"{tags.get(id(r), '')}{r.published or '-'} [{r.source or news_search.PROVIDERS.get(r.provider, r.provider)}] {r.title} — {r.snippet[:80]}"
            for r in results[:8]
        )

    def _tool_limit_up_history(self, args: dict) -> str:
        code, name = self._resolve(args)
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(LimitUpStock).filter(LimitUpStock.code.in_(code_candidates(code)))
                .order_by(LimitUpStock.trade_date.desc()).limit(10).all()
            )
            lines = [
                f"{r.trade_date} {r.continuous_days}板 首封{r.first_limit_time or '-'} 炸板{r.open_count or 0}次 原因:{r.reason or r.sector or '-'}"
                for r in rows
            ]
        return f"{name}({code}) 近期涨停：" + ("；".join(lines) or "无涨停记录")

    def _tool_theme(self, args: dict) -> str:
        from src.analyzers.theme_tracker import ThemeTracker

        tracker = ThemeTracker(self.config)
        themes = tracker.analyze_all()
        lines = [f"{t.dimension}｜{t.brief()}" for t in tracker.main_lines(themes, top=6)]
        text = "当前主线：" + ("；".join(lines) or "暂无明确主线")
        code, name = self._resolve(args, required=False)
        if code:
            role = tracker.stock_roles(themes).get(code)
            text += f"\n{name}({code})：" + (f"{role['dimension']}「{role['theme']}」（{role['phase']}）{role['role']}" if role else "不在最新交易日的涨停主线中")
        return text

    def _tool_market(self, args: dict) -> str:
        from src.services.market_context import build_market_facts

        return build_market_facts(self.config).text()

    def _tool_screening(self, args: dict) -> str:
        from src.strategy.screener import StrategyScreener

        picks = StrategyScreener(self.config).latest()
        if not picks:
            return "还没有策略选股结果"
        code, name = self._resolve(args, required=False)
        if code:
            hit = next((p for p in picks if p["code"] == code), None)
            if not hit:
                return f"{name}({code}) 不在最近一次（{picks[0]['trade_date']}）策略选股结果中"
            picks = [hit]
        return f"策略选股（{picks[0]['trade_date']}）：" + "；".join(
            f"{p['name']}({p['code']}) {'+'.join(p['labels'])} {p['score']:.0f}分"
            f"{'' if p['fits_regime'] else '（与大盘环境不匹配）'}：{'；'.join(p['reasons'])}" for p in picks[:15]
        )

    def _tool_position(self, args: dict) -> str:
        from src.services.real_portfolio import RealPortfolioService
        from src.trading.execution_service import ExecutionService

        code, _ = self._resolve(args, required=False)
        texts = []
        for label, positions in (("模拟盘持仓", ExecutionService(self.config).get_trading_snapshot(order_limit=1)["positions"]),
                                 ("实盘持仓", RealPortfolioService(self.config).positions())):
            if code:
                positions = [p for p in positions if bare_code(p["code"]) == code]
            texts.append(f"{label}：" + ("；".join(
                f"{p['name']}({p['code']}) {p['quantity']}股 成本{p['avg_cost']:.2f} 现价{p['market_price']:.2f} "
                f"止损{p['stop_loss']:.2f} 目标{p['target_price']:.2f} 浮盈{p['unrealized_pnl']:+.0f}" for p in positions
            ) or "无"))
        return "\n".join(texts)

    def _tool_diagnosis(self, args: dict) -> str:
        from src.services.fund_diagnosis import FundDiagnosisService
        from src.services.stock_diagnosis import StockDiagnosisService

        code, name, kind = self._resolve_kind(args)
        service = StockDiagnosisService(self.config) if kind == "stock" else FundDiagnosisService(self.config)
        result = service.latest(code)
        if not result:
            return f"{name}({code}) 还没有 AI 诊断记录"
        return (f"{result['name']}({code}) {result['created_at']} 诊断：{result['action_label']}，评分 {result['score']}；"
                f"{result.get('one_sentence', '')}" + (f"；护栏：{'；'.join(result['guardrails'])}" if result.get("guardrails") else ""))

    def _tool_watchlist(self, args: dict) -> str:
        from src.services.watchlist import WatchlistService

        rows = WatchlistService(self.config).overview()
        if not rows:
            return "自选股为空"
        return "自选股：" + "；".join(
            f"{r['name']}({r['code']})" + {"etf": "[ETF]", "index": "[指数]"}.get(r.get("kind", "stock"), "") + " " + (f"{r['close']}（{r['change_pct'] or 0:+.2f}%）" if r["close"] else "无行情")
            + (f" 诊断：{r['diagnosis']['action_label']} {r['diagnosis']['score']}分（{r['diagnosis']['created_at']}）" if r["diagnosis"] else " 未诊断")
            for r in rows
        )
