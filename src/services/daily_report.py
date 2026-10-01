"""
每日报告推送
汇总大盘复盘、交易信号、待确认订单、模拟盘账户和近期信号绩效，推送到已启用的机器人
（企业微信 / 钉钉 / 飞书）。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from loguru import logger

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import TradeOrder, TradeSignal
from src.notifier import broadcast, enabled_channels
from src.services.report_language import report_language, tr
from src.trading.constants import ORDER_STATUS_PENDING_CONFIRM

MAX_SIGNALS = 10
MAX_ORDERS = 10
REASON_MAX_CHARS = 40


def _pct(value: Any) -> str:
    return f"{value:+.2f}%" if isinstance(value, (int, float)) else "--"


def _rate(value: Any) -> str:
    return f"{value:.1f}%" if isinstance(value, (int, float)) else "--"


def _clean_reason(reason: str) -> str:
    """去掉盘前预测理由里的 ##TYPE##TIME##SRC## 结构标记，只保留核心逻辑。"""
    text = reason or ""
    if text.startswith("##"):
        text = text.rsplit("##", 1)[-1]
    text = text.strip()
    return text if len(text) <= REASON_MAX_CHARS else text[:REASON_MAX_CHARS] + "…"


class DailyReportService:
    """生成并推送每日报告。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        self.lang = report_language(self.config)

    def push(self) -> dict[str, Any]:
        """推送日报；没有启用任何渠道时不生成报告。"""
        if not enabled_channels(self.config, "daily_report"):
            return {"pushed": False, "reason": "未启用任何推送渠道"}
        title, content = self.build()
        results = broadcast(self.config, title, content, kind="daily_report")
        logger.info(f"每日报告推送结果: {results}")
        return {"pushed": any(results.values()), "channels": results}

    def build(self, overview: dict | None = None) -> tuple[str, str]:
        """返回 (标题, markdown 正文)。overview 为空时现场采集市场概况。"""
        today = date.today().strftime("%Y-%m-%d")
        self._regime_date = ""
        sections = [
            self._market_section(self._market_overview() if overview is None else overview),
            self._review_section(),
            self._theme_section(),
            self._signal_section(today),
            self._order_section(),
            self._account_section(),
            self._performance_section(),
            self._source_health_section(),
            tr(self.lang, "> 仅供学习研究，不构成投资建议", "> For study and research only; not investment advice"),
        ]
        return tr(self.lang, f"A股量化日报 {today}", f"A-share Quant Daily {today}"), "\n\n".join(s for s in sections if s)

    def _market_overview(self) -> dict:
        try:
            from src.collectors.stock_data import StockDataCollector

            return StockDataCollector(self.config).collect_market_overview() or {}
        except Exception as e:
            logger.warning(f"日报获取市场概况失败: {e}")
            return {}

    def _market_section(self, ov: dict) -> str:
        from src.analyzers.market_regime import MarketRegimeAnalyzer

        regime = MarketRegimeAnalyzer(self.config).analyze(overview=ov)
        regime_line = f"- {regime.summary()}" if regime.regime != "未知" else ""
        self._regime_date = regime.trade_date if regime_line else ""
        if not ov or not (ov.get("up_count") or ov.get("sh_index")):
            return "\n".join([tr(self.lang, "### 大盘复盘", "### Market Review"), regime_line or tr(self.lang, "暂无市场数据", "No market data")])
        lines = [tr(self.lang, "### 大盘复盘", "### Market Review"), *([regime_line] if regime_line else [])]
        indices = [
            f"{label} {ov[key]}（{_pct(ov.get(pct_key))}）"
            for label, key, pct_key in (("上证", "sh_index", "sh_change_pct"), ("深证", "sz_index", "sz_change_pct"), ("创业板", "cy_index", "cy_change_pct"))
            if ov.get(key)
        ]
        if indices:
            lines.append("- " + " | ".join(indices))
        lines.append(
            f"- 上涨 {ov.get('up_count', 0)} / 下跌 {ov.get('down_count', 0)} | "
            f"涨停 {ov.get('limit_up_count', 0)} / 跌停 {ov.get('limit_down_count', 0)}"
        )
        extra = [f"成交额 {ov['total_amount_yi']:.0f}亿"] if ov.get("total_amount_yi") else []
        if ov.get("northbound_net_yi"):
            extra.append(f"北向 {ov['northbound_net_yi']:+.1f}亿")
        if ov.get("market_emotion"):
            extra.append(f"情绪 {ov['market_emotion']}")
        if extra:
            lines.append("- " + " | ".join(extra))
        sectors = [s.get("name", "") if isinstance(s, dict) else str(s) for s in ov.get("top_sectors") or []]
        if any(sectors):
            lines.append("- 领涨板块：" + "、".join(s for s in sectors if s))
        return "\n".join(lines)

    def _review_section(self) -> str:
        """当天已生成的 LLM 大盘复盘（定时任务在推送前生成，这里只读取）。"""
        from src.services.market_review import MarketReviewService

        review = MarketReviewService(self.config).get(self._regime_date or date.today().strftime("%Y-%m-%d"))
        if not review or review.get("error"):
            return ""
        return tr(self.lang, "### AI 复盘与次日计划", "### AI Review and Next-day Plan") + "\n" + review["markdown"]

    def _theme_section(self) -> str:
        from src.analyzers.theme_tracker import ThemeTracker

        tracker = ThemeTracker(self.config)
        concept_themes = tracker.analyze(dimension="concept")
        industry_themes = tracker.analyze(dimension="industry")
        lines = [tr(self.lang, "### 主线梯队", "### Main Themes")]
        for label, group in (("题材", concept_themes), ("行业", industry_themes)):
            lines.extend(f"- {label}｜{t.brief()}" for t in tracker.main_lines(group, top=4))
        if len(lines) == 1:
            return ""
        themes = sorted(concept_themes + industry_themes, key=lambda t: -t.heat)
        cooling = [t.name for t in themes if t.phase in ("降温", "退潮")][:5]
        if cooling:
            lines.append(f"- 降温/退潮：{'、'.join(cooling)}")
        return "\n".join(lines)

    def _signal_section(self, today: str) -> str:
        with get_db_session(self.db_path) as session:
            # 今天的评分信号 + 下一个交易日的 AI 预测；都没有时取最近一天的信号
            query = session.query(TradeSignal).filter(TradeSignal.signal_type.in_(("premarket", "buy")))
            signals = query.filter(TradeSignal.signal_date >= today).order_by(TradeSignal.composite_score.desc()).all()
            if not signals:
                latest = session.query(TradeSignal.signal_date).order_by(TradeSignal.signal_date.desc()).limit(1).scalar()
                signals = query.filter(TradeSignal.signal_date == latest).order_by(TradeSignal.composite_score.desc()).all() if latest else []
            rows = [
                {
                    "date": s.signal_date, "code": s.code, "name": s.name or "", "type": s.signal_type,
                    "verdict": s.ai_verdict or "", "score": s.composite_score or 0, "reason": s.reason or "",
                    "plan": (s.entry_price, s.stop_loss_price, s.target_price),
                }
                for s in signals
            ]
        if not rows:
            return tr(self.lang, "### 交易信号\n暂无信号", "### Trading Signals\nNo signals")

        verdicts: dict[str, int] = {}
        for r in rows:
            key = r["verdict"] or "评分信号"
            verdicts[key] = verdicts.get(key, 0) + 1
        dates = "、".join(sorted({r["date"] for r in rows}))
        lines = [tr(self.lang, f"### 交易信号（{dates}）", f"### Trading Signals ({dates})"), "共 {} 条 | {}".format(len(rows), " · ".join(f"{k} {v}" for k, v in verdicts.items()))]
        for i, r in enumerate(rows[:MAX_SIGNALS], 1):
            kind = "AI预测" if r["type"] == "premarket" else "评分信号"
            head = f"{i}. **{r['name']}({r['code']})** {kind}"
            head += f"·{r['verdict']}" if r["verdict"] else f" {r['score']:.0f}分"
            entry, stop, target = r["plan"]
            plan = " ".join(f"{label}{v:.2f}" for label, v in (("买入", entry), ("止损", stop), ("目标", target)) if v)
            if plan:
                head += f" | {plan}"
            lines.append(head)
            reason = _clean_reason(r["reason"])
            if reason:
                lines.append(f"   {reason}")
        return "\n".join(lines)

    def _order_section(self) -> str:
        with get_db_session(self.db_path) as session:
            orders = (
                session.query(TradeOrder)
                .filter(TradeOrder.status == ORDER_STATUS_PENDING_CONFIRM)
                .order_by(TradeOrder.created_at.desc())
                .limit(MAX_ORDERS)
                .all()
            )
            lines = [
                f"- {'卖出' if o.side == 'sell' else '买入'} {o.name}({o.code}) {o.quantity}股 @{o.price:.2f}"
                + (f"（{o.risk_note}）" if o.side == "sell" and o.risk_note else "")
                for o in orders
            ]
        if not lines:
            return ""
        return "\n".join([tr(self.lang, "### 待确认订单", "### Pending Orders"), *lines,
                          tr(self.lang, "请在桌面端【模拟交易】页确认下单", "Confirm orders on the Paper Trading page")])

    def _account_section(self) -> str:
        try:
            from src.trading.execution_service import ExecutionService

            snapshot = ExecutionService(self.config).get_trading_snapshot(order_limit=1)
        except Exception as e:
            logger.warning(f"日报获取模拟盘账户失败: {e}")
            return ""
        acc = snapshot["account"]
        return (
            tr(self.lang, "### 模拟盘", "### Paper Account") + "\n"
            f"总资产 {acc['total_assets']:,.0f} | 可用资金 {acc['cash']:,.0f} | "
            f"持仓 {len(snapshot['positions'])} 只 | 浮动盈亏 {acc['unrealized_pnl']:+,.0f}"
        )

    @staticmethod
    def _source_health_section() -> str:
        from src.collectors.source_chain import source_health

        failing = source_health.failing()
        if not failing:
            return ""
        status_cn = {"circuit_open": "熔断中", "failing": "失败"}
        lines = [
            f"- {r['dataset']} / {r['source']}：{status_cn.get(r['status'], r['status'])}，"
            f"连续失败 {r['consecutive_failures']} 次（{r['last_error'][:40]}）"
            for r in failing[:8]
        ]
        return "\n".join(["### 数据源异常", *lines])

    def _performance_section(self) -> str:
        try:
            from src.services.signal_performance import SignalPerformanceService

            result = SignalPerformanceService(self.config).evaluate()
        except Exception as e:
            logger.warning(f"日报计算信号绩效失败: {e}")
            return ""
        overall = next((r for r in result["summary"] if r["dimension"] == "全部"), None)
        if not overall or not overall["evaluated"]:
            return ""
        return (
            f"### 近{result['lookback_days']}天信号绩效\n"
            f"已验证 {overall['evaluated']} 条 | 涨停命中率 {_rate(overall['limit_up_rate'])} | "
            f"次日胜率 {_rate(overall['win_rate_1d'])}，均收益 {_pct(overall['avg_return_1d'])} | "
            f"止损止盈模拟收益 {_pct(overall['simulated_avg'])}"
        )
