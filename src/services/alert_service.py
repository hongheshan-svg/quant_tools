"""
盘中提醒（参考 daily_stock_analysis 的实时告警中心 EventMonitor）

关注范围：今日交易信号与 AI 预测、模拟盘和实盘记账的持仓、自选股（watchlist 表）和 alerts.watchlist 中的股票。
每次行情采集后在交易时段内检查（用数据库中当日最新行情）：
- limit_up    封涨停（每只每天提醒一次）
- limit_open  炸板：上次检查时在涨停价，现在打开
- stop_loss   持仓跌破止损价（critical，免打扰时段也推送）
- take_profit 持仓达到目标价
- big_drop    跌幅超过 alerts.big_drop_pct
- near_stop   持仓现价距止损价不到 alerts.near_stop_pct（每只每天一次）
- 自定义规则 alerts.rules：price_cross（价格上破/下破）、change_pct（涨跌幅达到）、volume_spike（成交量达到近 20 日均量的倍数）、
  技术指标 ma_cross（价格上穿/下穿 N 日均线）、macd_cross / kdj_cross（金叉/死叉）、rsi（RSI 上穿/下穿阈值），
  指标用日线计算（当天的行即最新价），本地日线不足时先联网补齐；指标交叉每条规则每天只提醒一次
- regime_down 大盘环境降档（如均衡→防守，冰点为 critical）；regime_score_drop 大盘评分比前一交易日下降 alerts.regime_score_drop 分以上；
  两天都不用实时指数缓存，按同一口径比较，每天各提醒一次
规则可用 enabled: false 停用（不加入监控范围）；validate_rule() 校验规则，AlertService.test_rule() 试算一条规则（不写记录、不推送）。
同一提醒在冷却期内不重复；免打扰时段只推送 critical；同一批提醒合并为一条消息推送。
"""

from __future__ import annotations

import threading
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import func

from src import trading_calendar
from src.config_loader import load_config
from src.database.db import get_db_session
from src.analyzers.indicators import crossed, kdj, macd, rsi, sma
from src.database.models import AlertRecord, StockDaily, TradeSignal
from src.notifier import broadcast, enabled_channels
from src.notifier.noise import SEVERITY_RANK, NoiseFilter, in_quiet_hours
from src.utils.stock_code import bare_code, code_candidates, daily_limit_pct

LIMIT_TOLERANCE = 0.2       # 涨幅距涨停幅度 0.2 个百分点以内视为封板
LIMIT_OPEN_DROP = 0.5       # 从涨停价回落超过 0.5 个百分点视为炸板
VOLUME_AVG_DAYS = 20
INDICATOR_BARS = 120
MIN_INDICATOR_BARS = {"ma_cross": 0, "macd_cross": 35, "kdj_cross": 10, "rsi": 0}   # 均线和 RSI 按周期计算
SEVERITY_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
TYPE_LABELS = {
    "limit_up": "封涨停", "limit_open": "炸板", "stop_loss": "跌破止损", "take_profit": "达到目标价",
    "big_drop": "大跌", "price_cross": "价格突破", "change_pct": "涨跌幅", "volume_spike": "放量",
    "near_stop": "接近止损", "ma_cross": "均线突破", "macd_cross": "MACD交叉", "kdj_cross": "KDJ交叉", "rsi": "RSI",
    "regime_down": "大盘转弱", "regime_score_drop": "大盘评分下滑",
}
INDICATOR_TYPES = ("ma_cross", "macd_cross", "kdj_cross", "rsi")
DAILY_ONCE_TYPES = {"limit_up", "near_stop", "regime_down", "regime_score_drop", *INDICATOR_TYPES}
REGIME_LEVELS = {"冰点": 0, "防守": 1, "均衡": 2, "进攻": 3}
MARKET_CODE = "market"

DIRECTIONS = {
    "above_below": [["above", "上破/上穿"], ["below", "下破/下穿"]],
    "up_down": [["up", "上涨"], ["down", "下跌"]],
    "cross": [["golden", "金叉"], ["dead", "死叉"]],
}
# 规则类型定义：供 API 和 Web 动态渲染表单；字段 type 为 number 或 select
RULE_TYPES: dict[str, dict[str, Any]] = {
    "price_cross": {"label": "价格突破", "fields": [
        {"key": "direction", "label": "方向", "type": "select", "options": [["above", "上破"], ["below", "下破"]], "default": "above"},
        {"key": "price", "label": "价格（元）", "type": "number", "default": None},
    ]},
    "change_pct": {"label": "涨跌幅", "fields": [
        {"key": "direction", "label": "方向", "type": "select", "options": DIRECTIONS["up_down"], "default": "up"},
        {"key": "change_pct", "label": "涨跌幅（%）", "type": "number", "default": 5},
    ]},
    "volume_spike": {"label": "放量", "fields": [
        {"key": "multiplier", "label": "近 20 日均量的倍数", "type": "number", "default": 2},
    ]},
    "ma_cross": {"label": "均线突破", "fields": [
        {"key": "period", "label": "均线周期（日）", "type": "number", "default": 20},
        {"key": "direction", "label": "方向", "type": "select", "options": [["above", "上穿"], ["below", "下穿"]], "default": "above"},
    ]},
    "macd_cross": {"label": "MACD交叉", "fields": [
        {"key": "direction", "label": "方向", "type": "select", "options": DIRECTIONS["cross"], "default": "golden"},
    ]},
    "kdj_cross": {"label": "KDJ交叉", "fields": [
        {"key": "direction", "label": "方向", "type": "select", "options": DIRECTIONS["cross"], "default": "golden"},
    ]},
    "rsi": {"label": "RSI", "fields": [
        {"key": "period", "label": "RSI 周期（日）", "type": "number", "default": 6},
        {"key": "direction", "label": "方向", "type": "select", "options": [["above", "上穿"], ["below", "下穿"]], "default": "above"},
        {"key": "value", "label": "阈值", "type": "number", "default": 80},
    ]},
}
ACCOUNT_TYPES = {'account_concentration', 'account_drawdown', 'account_prices_stale'}
for _kind, _label, _default in [('account_concentration', '账户单标的集中度', 30), ('account_drawdown', '账户回撤', 10)]:
    RULE_TYPES[_kind] = {'label': _label, 'scopes': ['portfolio_account'], 'fields': [{'key': 'threshold', 'label': '阈值（%）', 'type': 'number', 'default': _default}]}
RULE_TYPES['account_prices_stale'] = {'label': '账户报价缺失或陈旧', 'scopes': ['portfolio_account'], 'fields': []}
RULE_TYPES['stop_risk'] = {'label': '持仓止损风险', 'scopes': ['portfolio_holdings'], 'fields': [{'key': 'threshold', 'label': '距止损阈值（%）', 'type': 'number', 'default': 2}]}
for _kind, _definition in RULE_TYPES.items():
    _definition.setdefault('scopes', ['stock', 'watchlist', 'portfolio_holdings'])
    TYPE_LABELS[_kind] = _definition['label']
INT_FIELDS = {"period"}
FIELD_ERRORS = {"price": "价格", "change_pct": "涨跌幅", "multiplier": "放量倍数", "period": "周期", "value": "RSI 阈值"}
VALID_SEVERITIES = ("info", "warning", "critical")


def normalize_code(raw: Any) -> str:
    """股票代码规范为 6 位（去掉 sh/sz/bj 前缀和 .SH 后缀）；无法识别时原样返回小写。"""
    from src.utils.stock_code import resolve_identity
    identity = resolve_identity(raw)
    if identity.kind != "stock":
        raise ValueError("盘中提醒仅支持 A 股个股")
    return identity.code


def validate_rule(rule: dict) -> dict:
    """校验并规范化一条提醒规则；非法时抛 ValueError（中文信息）。丢弃未知字段。"""
    if not isinstance(rule, dict):
        raise ValueError("规则格式不正确")
    kind = str(rule.get("type") or "").strip()
    if kind not in RULE_TYPES:
        raise ValueError(f"未知的规则类型 {kind or '（空）'}")
    scope = str(rule.get('scope') or 'stock')
    if scope not in RULE_TYPES[kind]['scopes']:
        raise ValueError('该规则类型不支持所选作用范围')
    code = normalize_code(rule.get('code')) if scope == 'stock' else ''
    if scope == 'stock' and not (len(code) == 6 and code.isdigit()):
        raise ValueError('股票代码必须是 6 位数字')
    out: dict[str, Any] = {"code": code, "type": kind}
    if scope != 'stock': out['scope'] = scope
    if scope.startswith('portfolio_'):
        account = str(rule.get('account') or 'paper').strip()
        if len(account) > 80 or account not in {'paper', 'real'} and not (account.startswith('real:') and account[5:].strip()):
            raise ValueError('账户应为 paper、real 或 real:账户名')
        out['account'] = account
    for f in RULE_TYPES[kind]["fields"]:
        key, raw = f["key"], rule.get(f["key"])
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            raw = f["default"]
            if kind == "rsi" and key == "value" and rule.get("direction") == "below":
                raw = 20
        label = FIELD_ERRORS.get(key, f["label"])
        if f["type"] == "select":
            allowed = [o[0] for o in f["options"]]
            if raw not in allowed:
                raise ValueError(f"{f['label']}必须是 {' / '.join(allowed)} 之一")
            out[key] = raw
            continue
        if raw is None or isinstance(raw, bool):
            raise ValueError(f"{label}必须大于 0")
        try:
            num = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{label}必须是数字") from None
        if not num > 0 or num != num or num == float("inf"):
            raise ValueError(f"{label}必须大于 0")
        if key in INT_FIELDS:
            if num != int(num):
                raise ValueError(f"{label}必须是整数")
            num = int(num)
        elif key == 'threshold' and num > 100:
            raise ValueError('风险阈值不能超过 100%')
        elif key == "value" and num > 100:
            raise ValueError("RSI 阈值不能超过 100")
        out[key] = num
    enabled = rule.get("enabled", True)
    out["enabled"] = enabled if isinstance(enabled, bool) else str(enabled).strip().lower() not in ("false", "0", "no", "off", "")
    out["note"] = str(rule.get("note") or "").strip()
    if rule.get("severity") in VALID_SEVERITIES:   # 沿用配置文件里手写的级别
        out["severity"] = rule["severity"]
    if rule.get("id") not in (None, ""):
        out["id"] = str(rule["id"])
    else:
        identity = {k: v for k, v in out.items() if k not in {"enabled", "note", "severity"}}
        out["id"] = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    return out


_noise = NoiseFilter()
_state_lock = threading.Lock()
_limit_state: dict[str, bool] = {}   # 代码 -> 上次检查时是否在涨停价
_state_date: date | None = None


@dataclass
class AlertEvent:
    code: str
    name: str
    alert_type: str
    severity: str
    message: str
    observed: float | None = None
    threshold: float | None = None
    rule_id: str = ""
    signal_context: dict | None = None

    @property
    def key(self) -> str:
        return f"{date.today()}|{self.code}|{self.alert_type}|{self.rule_id}"


class AlertService:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        cfg = self.config.get("alerts", {}) or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.big_drop_pct = float(cfg.get("big_drop_pct", -7))
        self.extra_watchlist = [bare_code(str(c)) for c in cfg.get("watchlist") or []]
        self.rules = []
        for raw in cfg.get('rules') if isinstance(cfg.get('rules'), list) else []:
            try:
                rule = validate_rule(raw)
                if rule["enabled"]:
                    self.rules.append(rule)
            except (ValueError, TypeError):
                logger.warning("忽略无效告警规则")
        self.near_stop_pct = float(cfg.get("near_stop_pct", 2))
        self.market_alerts = bool(cfg.get("market_regime", True))
        self.regime_score_drop = float(cfg.get("regime_score_drop", 15))
        min_severity = str(cfg.get("min_severity") or "info")
        self.min_severity = min_severity if min_severity in VALID_SEVERITIES else "info"
        _noise.cooldown = timedelta(minutes=float(cfg.get("cooldown_minutes", 30)))
        _noise.quiet_hours = (self.config.get("notifier", {}) or {}).get("quiet_hours") or []

    def run(self, now: datetime | None = None) -> dict[str, Any]:
        """检查并推送提醒；非交易时段直接跳过。"""
        now = now or datetime.now()
        if not self.enabled:
            return {"alerts": 0, "skipped": "未启用"}
        if not trading_calendar.in_trade_session(now):
            return {"alerts": 0, "skipped": "非交易时段"}
        self._sync_rules()
        can_push = bool(enabled_channels(self.config, "alert"))
        fresh, to_push = [], []
        events = self.evaluate()
        self._attach_signals(events, now)
        for ev in events:
            cooldown = timedelta(days=1) if ev.alert_type in DAILY_ONCE_TYPES else None
            reason = self._claim_event(ev, now, cooldown or _noise.cooldown)
            if reason == "冷却中":
                continue
            if not reason and SEVERITY_RANK.get(ev.severity, 0) < SEVERITY_RANK[self.min_severity]:
                reason = "低于推送级别"
            if not reason and not can_push:
                reason = "未启用推送"
            fresh.append((ev, reason))
            if not reason:
                to_push.append(ev)
        results = {}
        if to_push:
            lines = [f"- {SEVERITY_ICON.get(ev.severity, '')} {ev.message}" for ev in to_push]
            results = broadcast(self.config, f"盘中提醒 {now:%H:%M}", "\n".join(lines), kind="alert")
            pushed = any(results.values())
        else:
            pushed = False
        self._save(fresh, pushed, now, results)
        for ev, reason in fresh:
            logger.info(f"盘中提醒: {ev.message}" + (f"（未推送：{reason}）" if reason else ""))
        return {"alerts": len(fresh), "pushed": len(to_push) if pushed else 0}

    # ---------- 检查 ----------

    def _attach_signals(self, events: list[AlertEvent], now: datetime) -> None:
        from src.services.decision_signals import DecisionSignalService
        from src.utils.stock_code import diagnosis_code, StockCodeError
        summary = DecisionSignalService(self.config).active_summary([e.code for e in events], now)
        for event in events:
            try:
                code = diagnosis_code(event.code)
            except StockCodeError:
                continue
            signal = summary['signals'].get(code)
            event.signal_context = {'status': summary['status'], 'signal': signal}
            if summary['status'] == 'unknown':
                event.message += '；关联决策信号查询失败，状态未知'
            elif signal:
                event.message += f"；关联 AI 信号：{signal['action_label']}（诊断 #{signal['diagnosis_id'] or '已删除'}，有效至 {signal['expires_on'] or '未指定'}）"

    def watchlist(self, positions: list[dict[str, Any]] | None = None) -> dict[str, str]:
        """{代码: 名称}：今日信号、模拟盘持仓、配置中的关注股票和规则股票。"""
        today = date.today().strftime("%Y-%m-%d")
        watch: dict[str, str] = {}
        with get_db_session(self.db_path) as session:
            for code, name in session.query(TradeSignal.code, TradeSignal.name).filter(TradeSignal.signal_date == today).all():
                watch[bare_code(code)] = name or ""
        for pos in self._positions() if positions is None else positions:
            watch.setdefault(bare_code(pos["code"]), pos.get("name", ""))
        for code in [*self.extra_watchlist, *(bare_code(str(r["code"])) for r in self.rules if r.get('scope', 'stock') == 'stock')]:
            watch.setdefault(code, "")
        try:
            from src.services.watchlist import WatchlistService

            for item in WatchlistService(self.config).list():
                if item.get("kind", "stock") != "stock":
                    continue  # ETF/指数没有 stock_daily 行情，盘中提醒跳过
                watch.setdefault(item["code"], item["name"])
        except Exception as e:
            logger.debug(f"读取自选股失败: {e}")
        for rule in self.rules:
            if rule.get('scope') == 'portfolio_holdings':
                for item in self._scope_members(rule):
                    watch.setdefault(item['code'], item.get('name') or '')
        return watch

    def _scope_members(self, rule):
        if rule.get('scope', 'stock') == 'stock':
            return [{'code': rule['code']}]
        if rule.get('scope') == 'watchlist':
            from src.services.watchlist import WatchlistService
            return [item for item in WatchlistService(self.config).list() if item.get('kind', 'stock') == 'stock'] + [{'code': code} for code in self.extra_watchlist if code]
        if rule.get('scope') == 'portfolio_holdings':
            from src.services.portfolio_risk import PortfolioRiskService
            report = PortfolioRiskService(self.config, account=rule['account']).report()
            if rule['type'] == 'stop_risk': return report['positions']
            return [item for item in report['positions'] if len(bare_code(item['code'])) == 6 and bare_code(item['code'])[0] in '03689']
        return []

    def _account_outcome(self, rule):
        from src.services.portfolio_risk import PortfolioRiskService
        report = PortfolioRiskService(self.config, account=rule['account']).report()
        kind, threshold = rule['type'], rule.get('threshold')
        if kind == 'account_prices_stale':
            invalid = [item['code'] for item in report['positions'] if item['price_quality']['status'] != 'available']
            return bool(invalid), '报价缺失或陈旧：' + '、'.join(invalid) if invalid else '持仓报价均有效', len(invalid), 0
        if kind == 'account_concentration':
            if report['quality']['valuation'] != 'available' or not report['cash_known']:
                return None
            value = max((item['weight'] for item in report['positions']), default=0)
        else:
            drawdown = report['drawdown']
            if drawdown.get('quality', {}).get('status') != 'available' or drawdown.get('current_drawdown') is None:
                return None
            value = abs(min(0, drawdown['current_drawdown']))
        return value >= threshold, f"{TYPE_LABELS[kind]} {value:.2f}%（阈值 {threshold:g}%）", value, threshold

    def _account_events(self):
        events = []
        for rule in self.rules:
            if rule.get('scope') != 'portfolio_account': continue
            outcome = self._account_outcome(rule)
            if outcome and outcome[0]:
                _, message, observed, threshold = outcome
                events.append(AlertEvent('account:' + rule['account'], rule['account'], rule['type'], rule.get('severity', 'warning'), rule['account'] + '：' + message, observed, threshold, rule['id']))
        return events

    def _positions(self) -> list[dict[str, Any]]:
        """模拟盘持仓 + 实盘记账持仓（account=real）。"""
        positions: list[dict[str, Any]] = []
        try:
            from src.trading.execution_service import ExecutionService

            positions += ExecutionService(self.config).get_trading_snapshot(order_limit=1)["positions"]
        except Exception as e:
            logger.debug(f"读取模拟盘持仓失败: {e}")
        try:
            from src.services.real_portfolio import RealPortfolioService

            positions += RealPortfolioService(self.config).positions()
        except Exception as e:
            logger.debug(f"读取实盘持仓失败: {e}")
        return positions

    def evaluate(self) -> list[AlertEvent]:
        global _state_date
        today = date.today()
        with _state_lock:
            if _state_date != today:
                _limit_state.clear()
                _state_date = today
        events: list[AlertEvent] = [*self._regime_events(), *self._account_events()]
        positions = self._positions()
        watch = self.watchlist(positions)
        if not watch:
            return events
        quotes = self._latest_quotes(list(watch), today.strftime("%Y-%m-%d"))
        for code, q in quotes.items():
            name = watch.get(code) or q["name"]
            events.extend(self._limit_events(code, name, q))
            if q["change_pct"] <= self.big_drop_pct:
                events.append(AlertEvent(code, name, "big_drop", "warning", f"{name}({code}) 大跌 {q['change_pct']:+.2f}%，现价 {q['price']}",
                                         q["change_pct"], self.big_drop_pct))
        for pos in positions:
            code = bare_code(pos["code"])
            q = quotes.get(code)
            if not q:
                continue
            name = pos.get("name") or q["name"]
            holder = "实盘持仓" if pos.get("account") == "real" else "持仓"
            if q["price"] <= pos["stop_loss"]:
                events.append(AlertEvent(code, name, "stop_loss", "critical",
                                         f"{holder} {name}({code}) 跌破止损价 {pos['stop_loss']:.2f}，现价 {q['price']}，"
                                         + ("请按计划止损" if pos.get("account") == "real" else "请确认卖出订单"), q["price"], pos["stop_loss"]))
            elif pos["stop_loss"] and q["price"] <= pos["stop_loss"] * (1 + self.near_stop_pct / 100):
                gap = (q["price"] / pos["stop_loss"] - 1) * 100
                events.append(AlertEvent(code, name, "near_stop", "warning",
                                         f"{holder} {name}({code}) 接近止损价 {pos['stop_loss']:.2f}（还差 {gap:.1f}%），现价 {q['price']}",
                                         q["price"], pos["stop_loss"]))
            elif q["price"] >= pos["target_price"]:
                events.append(AlertEvent(code, name, "take_profit", "info",
                                         f"{holder} {name}({code}) 达到目标价 {pos['target_price']:.2f}，现价 {q['price']}", q["price"], pos["target_price"]))
        custom = self._rule_events(quotes, watch)
        custom_stops = {event.code for event in custom if event.alert_type == 'stop_risk'}
        events = [event for event in events if event.code not in custom_stops or event.alert_type not in {'stop_loss', 'near_stop'}]
        events.extend(custom)
        return events

    def _latest_quotes(self, codes: list[str], today: str) -> dict[str, dict[str, Any]]:
        cands = {c: code_candidates(c) for c in codes}
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(StockDaily.code, StockDaily.name, StockDaily.close, StockDaily.change_pct, StockDaily.volume)
                .filter(StockDaily.trade_date == today, StockDaily.code.in_([x for v in cands.values() for x in v]))
                .all()
            )
        quotes = {}
        for code, name, price, change, volume in rows:
            if price and change is not None:
                quotes[bare_code(code)] = {"name": name or "", "price": price, "change_pct": change, "volume": volume}
        return quotes

    def _limit_events(self, code: str, name: str, q: dict[str, Any]) -> list[AlertEvent]:
        limit = daily_limit_pct(code, name) * 100
        at_limit = q["change_pct"] >= limit - LIMIT_TOLERANCE
        with _state_lock:
            was_at_limit = _limit_state.get(code, False)
            _limit_state[code] = at_limit
        if at_limit and not was_at_limit:
            return [AlertEvent(code, name, "limit_up", "info", f"{name}({code}) 封涨停 {q['change_pct']:+.2f}%", q["change_pct"], limit)]
        if was_at_limit and q["change_pct"] < limit - LIMIT_OPEN_DROP:
            return [AlertEvent(code, name, "limit_open", "warning", f"{name}({code}) 炸板，涨幅回落到 {q['change_pct']:+.2f}%", q["change_pct"], limit)]
        return []

    def _rule_events(self, quotes: dict[str, dict[str, Any]], watch: dict[str, str]) -> list[AlertEvent]:
        events = []
        for rule in self.rules:
            if rule.get('scope') == 'portfolio_account': continue
            seen = set()
            for member in self._scope_members(rule):
                code = bare_code(member['code'])
                if code in seen: continue
                seen.add(code)
                q = quotes.get(code)
                if not q and rule['type'] != 'stop_risk': continue
                name = watch.get(code) or member.get('name') or (q or {}).get('name') or code
                if rule['type'] == 'stop_risk':
                    gap = member.get('stop_gap')
                    outcome = (gap <= rule['threshold'], f"{rule['account']} {name}({code}) 距止损 {gap:.2f}%", gap, rule['threshold']) if gap is not None else None
                else:
                    outcome = self._check_rule(rule, code, name, q)
                if outcome and outcome[0]:
                    _, text, observed, threshold = outcome
                    events.append(AlertEvent(code, name, rule['type'], rule.get('severity', 'warning'), text, observed, threshold, rule['id']))
        return events

    def _check_rule(self, rule: dict, code: str, name: str, q: dict[str, Any]) -> tuple[bool, str, float | None, float | None] | None:
        """评估单条规则：(是否触发, 文字, 观测值, 阈值)；文字在触发时是提醒内容，未触发时说明当前值；无法评估（未知类型、日线不足）返回 None。"""
        kind = rule.get("type")
        direction = rule.get("direction", "above" if kind == "price_cross" else "up")
        if kind == "price_cross":
            price = float(rule["price"])
            hit = q["price"] >= price if direction == "above" else q["price"] <= price
            word = "上破" if direction == "above" else "下破"
            text = f"{name}({code}) 价格{word} {price}，现价 {q['price']}" if hit else f"现价 {q['price']:.2f}，未{word} {price:.2f}"
            return hit, text, q["price"], price
        if kind == "change_pct":
            pct = abs(float(rule["change_pct"]))
            hit = q["change_pct"] >= pct if direction == "up" else q["change_pct"] <= -pct
            sign = "+" if direction == "up" else "-"
            text = (f"{name}({code}) 涨跌幅达到 {q['change_pct']:+.2f}%（阈值 {sign}{pct}%）" if hit
                    else f"当前涨跌幅 {q['change_pct']:+.2f}%，未达到 {sign}{pct}%")
            return hit, text, q["change_pct"], pct
        if kind == "volume_spike":
            ratio = self._volume_ratio(code, q.get("volume"))
            multiplier = float(rule.get("multiplier", 2))
            hit = ratio is not None and ratio >= multiplier
            text = (f"{name}({code}) 放量，成交量为近 {VOLUME_AVG_DAYS} 日均量的 {ratio or 0:.1f} 倍" if hit
                    else (f"当前成交量为近 {VOLUME_AVG_DAYS} 日均量的 {ratio:.1f} 倍，未达到 {multiplier:g} 倍" if ratio is not None
                          else "没有足够的历史成交量，无法计算量比"))
            return hit, text, ratio, multiplier
        if kind in INDICATOR_TYPES:
            return self._indicator_check(code, name, rule, q["price"])
        return None

    def test_rule(self, rule: dict) -> dict[str, Any]:
        """试算一条规则：忽略交易时段、冷却和每天一次的限制，不写提醒记录、不推送。"""
        rule = validate_rule(rule)
        if rule.get('scope') == 'portfolio_account':
            outcome = self._account_outcome(rule)
            return {'triggered': bool(outcome and outcome[0]), 'available': outcome is not None, 'message': outcome[1] if outcome else '风险数据质量不足，无法评估', 'quote': None}
        if rule.get('scope', 'stock') != 'stock':
            members = self._scope_members(rule)
            quotes = self._latest_quotes([bare_code(item['code']) for item in members], date.today().strftime('%Y-%m-%d'))
            original = self.rules
            try:
                self.rules = [rule]
                events = self._rule_events(quotes, {item['code']: item.get('name') or '' for item in members})
            finally:
                self.rules = original
            evaluated = sum(item.get('stop_gap') is not None for item in members) if rule['type'] == 'stop_risk' else len(quotes)
            return {'triggered': bool(events), 'quote': None, 'members': len({item['code'] for item in members}), 'evaluated': evaluated, 'message': '；'.join(event.message for event in events) or f'范围内 {len(members)} 个标的，有效报价 {evaluated} 个，无触发；缺少报价的标的无法评估'}
        code = rule["code"]
        today = date.today().strftime("%Y-%m-%d")
        quotes = self._latest_quotes([code], today)
        if not quotes:   # 非交易日或盘前没有当天行情时，用库里最近一个交易日的行情试算
            with get_db_session(self.db_path) as session:
                latest = session.query(func.max(StockDaily.trade_date)).filter(StockDaily.code.in_(code_candidates(code))).scalar()
            quotes = self._latest_quotes([code], latest) if latest else {}
        q = quotes.get(code)
        if not q:
            return {"triggered": False, "message": "没有取到行情", "quote": None}
        quote = {"price": q["price"], "change_pct": q["change_pct"]}
        outcome = self._check_rule(rule, code, q["name"], q)
        if outcome is None:
            return {"triggered": False, "message": "日线数据不足，无法计算该指标", "quote": quote}
        hit, text, _, _ = outcome
        return {"triggered": bool(hit), "message": text, "quote": quote}

    def _daily_bars(self, code: str, need: int) -> list:
        """按日期升序的日线（含当天实时行），不足 need 根时先联网补齐一次。"""
        def load() -> list:
            with get_db_session(self.db_path) as session:
                rows = (
                    session.query(StockDaily.trade_date, StockDaily.high, StockDaily.low, StockDaily.close)
                    .filter(StockDaily.code.in_(code_candidates(code)), StockDaily.close > 0)
                    .order_by(StockDaily.trade_date.desc()).limit(INDICATOR_BARS * 2).all()
                )
            by_date = {r.trade_date: r for r in rows}
            return [by_date[d] for d in sorted(trading_calendar.trade_days_only(by_date))][-INDICATOR_BARS:]

        bars = load()
        if len(bars) < need:
            from src.collectors.daily_history import ensure_daily_history

            if ensure_daily_history(code, self.db_path):
                bars = load()
        return bars

    def _indicator_check(self, code: str, name: str, rule: dict, price: float) -> tuple[bool, str, float | None, float | None] | None:
        """技术指标规则：(是否触发, 提醒文字, 观测值, 阈值)；日线不足时返回 None。"""
        kind = rule["type"]
        period = int(rule.get("period", 6 if kind == "rsi" else 20))
        need = max(MIN_INDICATOR_BARS[kind], period + 2)
        bars = self._daily_bars(code, need)
        if len(bars) < need:
            logger.debug(f"技术指标提醒 {code} {kind}：日线 {len(bars)} 根，不足 {need} 根")
            return None
        closes = [b.close for b in bars]
        if kind == "ma_cross":
            up = rule.get("direction", "above") == "above"
            ma_now, ma_prev = sma(closes, period), sma(closes[:-1], period)
            hit = crossed(closes[-2], ma_prev, closes[-1], ma_now, "up" if up else "down")
            word = "上穿" if up else "下穿"
            text = f"{name}({code}) 价格{word} MA{period}（{ma_now:.2f}），现价 {price}" if hit else f"现价 {price}，MA{period} 为 {ma_now:.2f}，未{word}"
            return hit, text, price, round(ma_now, 2)
        if kind == "rsi":
            up = rule.get("direction", "above") == "above"
            level = float(rule.get("value", 80 if up else 20))
            now, prev = rsi(closes, period), rsi(closes[:-1], period)
            hit = crossed(prev, level, now, level, "up" if up else "down")
            word = "上穿" if up else "下穿"
            text = f"{name}({code}) RSI{period} {word} {level:g}（{now:.1f}）" if hit else f"RSI{period} 当前 {now:.1f}，未{word} {level:g}"
            return hit, text, round(now, 1), level
        golden = rule.get("direction", "golden") == "golden"
        label = "金叉" if golden else "死叉"
        if kind == "macd_cross":
            dif, dea = macd(closes)
            hit = crossed(dif[-2], dea[-2], dif[-1], dea[-1], "up" if golden else "down")
            state = f"DIF {dif[-1]:.3f}，DEA {dea[-1]:.3f}"
            return hit, f"{name}({code}) MACD {label}（{state}）" if hit else f"MACD 未出现{label}（{state}）", round(dif[-1], 3), round(dea[-1], 3)
        highs = [b.high or b.close for b in bars]
        lows = [b.low or b.close for b in bars]
        k, d, _ = kdj(highs, lows, closes)
        hit = crossed(k[-2], d[-2], k[-1], d[-1], "up" if golden else "down")
        state = f"K {k[-1]:.1f}，D {d[-1]:.1f}"
        return hit, f"{name}({code}) KDJ {label}（{state}）" if hit else f"KDJ 未出现{label}（{state}）", round(k[-1], 1), round(d[-1], 1)

    def _regime_events(self) -> list[AlertEvent]:
        """大盘环境比前一交易日降档或评分明显下滑。"""
        if not self.market_alerts:
            return []
        try:
            from src.analyzers.market_regime import MarketRegimeAnalyzer

            today = date.today()
            analyzer = MarketRegimeAnalyzer(self.config)
            now_r = analyzer.analyze(overview={})
            if now_r.regime not in REGIME_LEVELS or now_r.trade_date != today.strftime("%Y-%m-%d"):
                return []
            prev_r = analyzer.analyze(trade_date=(today - timedelta(days=1)).strftime("%Y-%m-%d"), overview={})
        except Exception as e:
            logger.debug(f"大盘环境提醒检查失败: {e}")
            return []
        if prev_r.regime not in REGIME_LEVELS:
            return []
        change = f"（{prev_r.score:.0f}→{now_r.score:.0f}分）"
        if REGIME_LEVELS[now_r.regime] < REGIME_LEVELS[prev_r.regime]:
            severity = "critical" if now_r.regime == "冰点" else "warning"
            text = f"大盘环境由「{prev_r.regime}」转为「{now_r.regime}」{change}，新开仓仓位按 ×{now_r.position_factor:.1f} 控制"
            return [AlertEvent(MARKET_CODE, "大盘", "regime_down", severity, text, now_r.score, prev_r.score, now_r.regime)]
        if prev_r.score - now_r.score >= self.regime_score_drop:
            text = f"大盘评分比前一交易日下降 {prev_r.score - now_r.score:.0f} 分{change}，仍为「{now_r.regime}」，注意控制仓位"
            return [AlertEvent(MARKET_CODE, "大盘", "regime_score_drop", "warning", text, now_r.score, prev_r.score)]
        return []

    def _volume_ratio(self, code: str, volume: float | None) -> float | None:
        if not volume:
            return None
        today = date.today().strftime("%Y-%m-%d")
        with get_db_session(self.db_path) as session:
            history = [
                v for (v,) in session.query(StockDaily.volume)
                .filter(StockDaily.code.in_(code_candidates(code)), StockDaily.trade_date < today, StockDaily.volume > 0)
                .order_by(StockDaily.trade_date.desc()).limit(VOLUME_AVG_DAYS).all()
            ]
        avg = sum(history) / len(history) if history else 0
        return volume / avg if avg else None

    def _claim_event(self, ev: AlertEvent, now: datetime, cooldown: timedelta) -> str:
        """SQLite 条件 upsert 原子领取冷却键，重启及多进程不会重复推送。"""
        from sqlalchemy.dialects.sqlite import insert
        from src.database.models import AlertCooldown
        key = f"{now:%Y-%m-%d}|{ev.code}|{ev.alert_type}|{ev.rule_id}"
        statement = insert(AlertCooldown).values(key=key, last_triggered_at=now)
        statement = statement.on_conflict_do_update(index_elements=[AlertCooldown.key],
            set_={"last_triggered_at": now}, where=AlertCooldown.last_triggered_at <= now - cooldown)
        with get_db_session(self.db_path) as session:
            claimed = session.execute(statement).rowcount
        if not claimed:
            return "冷却中"
        if ev.severity != "critical" and in_quiet_hours(now, _noise.quiet_hours):
            return "免打扰时段"
        return ""

    def _sync_rules(self) -> None:
        from src.database.models import AlertRule
        with get_db_session(self.db_path) as session:
            current = set()
            for raw in (self.config.get("alerts") or {}).get("rules") or []:
                try:
                    rule = validate_rule(raw)
                except (ValueError, TypeError) as error:
                    from src.utils.redaction import redact_text
                    logger.warning("忽略无效告警规则: {}", redact_text(error, 200))
                    continue
                payload = json.dumps(rule, ensure_ascii=False, sort_keys=True)
                rule_id = str(rule.get("id") or hashlib.sha256(payload.encode()).hexdigest()[:24])
                current.add(rule_id)
                row = session.get(AlertRule, rule_id)
                if row is None:
                    row = AlertRule(id=rule_id)
                    session.add(row)
                row.rule_json, row.enabled, row.updated_at = payload, rule["enabled"], datetime.now()
            for row in session.query(AlertRule):
                if row.id not in current:
                    row.enabled = False

    def _save(self, fresh: list[tuple[AlertEvent, str]], pushed: bool, now: datetime, channel_results: dict | None = None) -> None:
        if not fresh:
            return
        with get_db_session(self.db_path) as session:
            for ev, reason in fresh:
                session.add(AlertRecord(
                    code=ev.code, name=ev.name, alert_type=ev.alert_type, severity=ev.severity, message=ev.message,
                    observed=ev.observed, threshold=ev.threshold, notified=pushed and not reason,
                    suppressed_reason=reason or ("" if pushed else "推送失败"), triggered_at=now,
                    event_key=f"{now:%Y-%m-%d}|{ev.code}|{ev.alert_type}|{ev.rule_id}", rule_id=ev.rule_id,
                    channel_results_json=json.dumps(channel_results if not reason else {}, ensure_ascii=False),
                    signal_context_json=json.dumps(ev.signal_context, ensure_ascii=False) if ev.signal_context else None,
                ))

    def digest(self, day: str | None = None, push: bool = True) -> dict[str, Any]:
        """汇总某个交易日（默认今天）的提醒记录；有记录且 push 为真时推送「盘中提醒日报」（kind=alert）。"""
        day = day or date.today().strftime("%Y-%m-%d")
        start = datetime.strptime(day, "%Y-%m-%d")
        with get_db_session(self.db_path) as session:
            rows = session.query(AlertRecord).filter(
                AlertRecord.triggered_at >= start, AlertRecord.triggered_at < start + timedelta(days=1),
            ).order_by(AlertRecord.triggered_at).all()
            records = [(r.code, r.name, r.alert_type, r.severity, r.message, r.triggered_at) for r in rows]
        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        stocks: dict[str, dict[str, Any]] = {}
        critical: list[dict[str, Any]] = []
        for code, name, kind, severity, message, at in records:
            label = TYPE_LABELS.get(kind, kind)
            by_type[label] = by_type.get(label, 0) + 1
            by_severity[severity] = by_severity.get(severity, 0) + 1
            if code != MARKET_CODE:
                entry = stocks.setdefault(code, {"code": code, "name": name or "", "count": 0})
                entry["count"] += 1
            if severity == "critical":
                critical.append({"time": at.strftime("%H:%M"), "code": code, "name": name or "", "type": label, "message": message})
        top_stocks = sorted(stocks.values(), key=lambda x: (-x["count"], x["code"]))[:5]
        result: dict[str, Any] = {
            "day": day, "total": len(records), "by_type": by_type, "by_severity": by_severity,
            "top_stocks": top_stocks, "critical": critical, "markdown": "", "pushed": False,
        }
        if not records:
            return result
        lines = [f"## 盘中提醒日报 {day}", "", f"共 {len(records)} 条提醒"]
        if by_severity:
            lines.append("- 按级别：" + "、".join(
                f"{SEVERITY_ICON.get(k, '')}{k} {by_severity[k]}" for k in ("critical", "warning", "info") if k in by_severity))
        lines.append("- 按类型：" + "、".join(f"{k} {v}" for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])))
        if top_stocks:
            lines += ["", "### 提醒最多的股票"]
            lines += [f"- {x['name']}({x['code']}) {x['count']} 次" for x in top_stocks]
        if critical:
            lines += ["", "### 紧急提醒"]
            lines += [f"- {c['time']} {c['message']}" for c in critical]
        result["markdown"] = "\n".join(lines)
        if push and enabled_channels(self.config, "alert"):
            results = broadcast(self.config, f"盘中提醒日报 {day}", result["markdown"], kind="alert")
            result["pushed"] = any(results.values())
        return result

    def recent(self, limit: int = 200, *, offset: int = 0, severity: str = "", code: str = "", channel: str = "") -> list[dict[str, Any]]:
        with get_db_session(self.db_path) as session:
            query = session.query(AlertRecord)
            if severity:
                query = query.filter(AlertRecord.severity == severity)
            if code:
                query = query.filter(AlertRecord.code == bare_code(code))
            if channel:
                query = query.filter(AlertRecord.channel_results_json.contains('"' + channel + '"'))
            rows = query.order_by(AlertRecord.triggered_at.desc(), AlertRecord.id.desc()).offset(offset).limit(limit).all()
            return [
                {"id": r.id, "time": r.triggered_at.strftime("%Y-%m-%d %H:%M"), "code": r.code, "name": r.name,
                 "type": TYPE_LABELS.get(r.alert_type, r.alert_type), "severity": r.severity, "message": r.message,
                 "event_key": r.event_key, "rule_id": r.rule_id, "channels": json.loads(r.channel_results_json or "{}"),
                 "signal_context": json.loads(r.signal_context_json or 'null'),
                 "notified": r.notified, "reason": r.suppressed_reason or ""}
                for r in rows
            ]


def reset_state() -> None:
    """清空进程内的提醒状态（测试用）。"""
    global _state_date
    _noise.reset()
    with _state_lock:
        _limit_state.clear()
        _state_date = None
