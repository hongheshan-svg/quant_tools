"""
全市场策略选股（参考 daily_stock_analysis 的 screening：全市场快照 → 硬过滤 → 策略规则 → 打分排序）

流程：
1. 最新交易日的全市场行情（stock_daily）→ 股票池过滤（config/stock_pool.yaml：名称关键词、黑名单、价格、流通市值）
2. 取候选股近 60 个交易日的日线，计算均线、量比、20 日平台、回撤、近期涨停次数等特征；
   个股题材取近 60 天涨停记录里的行业和同花顺题材，与当前主线（ThemeTracker）匹配
3. 逐个策略判断是否入选并打分（0~100），标注策略是否适配当前的量化大盘环境
4. 同时被多个策略选中的股票合并加分，按「适配环境 → 得分」排序，保存到 strategy_pick

内置策略（参数可在 settings.yaml 的 screening.strategies.<策略标识> 覆盖，enabled: false 关闭）：
- volume_breakout 放量突破、strong_close 强势未板、dragon_pullback 龙回头、theme_follow 主线补涨
- trend_pullback 缩量回踩、oversold_rebound 超跌反弹
选出的股票是 AI 涨停预测的候选来源之一；performance() 统计各策略选股的次日表现。
策略权重：最近 30 天内做过历史回测（strategy_backtest）时，按回测得出的权重（0.8~1.2）调整各策略得分；
回测本身用 point_in_time=True 选股：权重不生效，大盘环境也不读进程内的实时行情缓存。
"""

from __future__ import annotations

import json
import hashlib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from loguru import logger
from sqlalchemy import func

from src import trading_calendar
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import LimitUpStock, StockDaily, StrategyPick
from src.utils.stock_code import bare_code, code_candidates, daily_limit_pct
from src.strategy.data_quality import equity_code, finite_number, iso_date, prices_comparable, row_priority

STOCK_POOL_CONFIG_PATH = "config/stock_pool.yaml"
HISTORY_BARS = 61            # 今日 + 前 60 个交易日
HISTORY_CALENDAR_DAYS = 100  # 取 60 个交易日所需的自然日跨度
THEME_LOOKBACK_DAYS = 60     # 个股题材取近 60 天的涨停记录
MAIN_LINE_TOP = 8
MIN_UNIVERSE = 3000          # 当天行情少于该数量时提示不是全市场
MIN_HISTORY_COVERAGE = 0.5   # 有 20 日以上日线的股票占比低于该值时提示补齐历史
PRE_FILTER_AMOUNT = 3e7      # 成交额 3000 万以下直接跳过（所有策略的要求都更高）
LIMIT_TOLERANCE = 0.3        # 涨幅距涨停价 0.3 个百分点以内视为涨停
MULTI_STRATEGY_BONUS = 5
CHUNK = 500
WEIGHTS_MAX_AGE_DAYS = 30   # 超过 30 天的回测不再用于调整策略权重
BACKTEST_ENGINE_VERSION = "a-share-t1-v2"


# ---------- 特征 ----------

@dataclass
class Features:
    code: str
    name: str
    close: float
    change_pct: float
    amount: float
    turnover: float | None = None
    limit_pct: float = 10.0
    bars: int = 1
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    vol_ratio: float | None = None     # 今日成交额 / 前 5 日平均成交额（近似量比）
    high_20: float | None = None       # 前 20 日最高价
    range_20: float | None = None      # 前 20 日振幅 %
    ret_20: float | None = None        # 20 日涨幅 %
    pullback_15: float | None = None   # 距近 15 日最高价 %
    limit_ups_15: int = 0              # 前 15 个交易日的涨停次数
    close_pos: float | None = None     # 收盘价在当日振幅中的位置 0~1
    pe: float | None = None
    pb: float | None = None
    roe: float | None = None
    profit_yoy: float | None = None
    revenue_yoy: float | None = None
    dividend_yield: float | None = None
    circ_mv: float | None = None
    data_quality: dict = field(default_factory=dict)
    industry: str = ""
    fundamentals: dict = field(default_factory=dict)
    themes: dict[str, float] = field(default_factory=dict)  # 所属的当前主线 → 热度

    @property
    def is_limit_up(self) -> bool:
        return self.change_pct >= self.limit_pct - LIMIT_TOLERANCE


def compute_features(code: str, name: str, bars: list) -> Features | None:
    """bars：按日期升序的日线（最后一根为选股日），需有 close/high/low/volume/amount/change_pct/turnover 属性。"""
    if not bars:
        return None
    # 无效价格不能从均线中删掉后压缩周期；只使用最后一段连续、可比较的价格。
    clean = []
    for bar in bars:
        close = finite_number(bar.close)
        if close is None or close <= 0:
            clean = []
            continue
        if clean and not prices_comparable(clean[-1], bar):
            clean = []
        clean.append(bar)
    if not clean or clean[-1] is not bars[-1]:
        return None
    bars, today = clean, clean[-1]
    change, amount = finite_number(today.change_pct), finite_number(today.amount)
    if change is None or amount is None or amount <= 0:
        return None
    limit_pct = daily_limit_pct(code, name) * 100
    f = Features(code=code, name=name, close=float(today.close), change_pct=change, amount=amount,
                 turnover=finite_number(today.turnover), limit_pct=limit_pct, bars=len(bars))
    closes = [float(b.close) for b in bars]
    for n in (5, 10, 20, 60):
        if len(closes) >= n:
            setattr(f, f"ma{n}", sum(closes[-n:]) / n)

    prev = bars[:-1]
    # 各数据源的成交量单位不一致（股/手），成交额统一为元，用成交额比近似量比
    base = [finite_number(b.amount) for b in prev[-5:]]
    if len(base) == 5 and all(value is not None and value > 0 for value in base):
        f.vol_ratio = amount / (sum(base) / len(base))

    last20 = prev[-20:]
    if len(last20) >= 20:
        highs = [finite_number(b.high) for b in last20]
        lows = [finite_number(b.low) for b in last20]
        if all(h is not None and l is not None and 0 < l <= float(b.close) <= h for b, h, l in zip(last20, highs, lows)):
            f.high_20 = max(highs)
            f.range_20 = (max(highs) / min(lows) - 1) * 100
        f.ret_20 = (today.close / last20[0].close - 1) * 100
    if len(bars) >= 10:
        highs = [finite_number(b.high) for b in bars[-15:]]
        if all(h is not None and h > 0 for h in highs):
            f.pullback_15 = (f.close / max(highs) - 1) * 100
    f.limit_ups_15 = sum(1 for b in prev[-15:] if (finite_number(b.change_pct) or 0) >= limit_pct - LIMIT_TOLERANCE)
    high, low = finite_number(today.high), finite_number(today.low)
    if high is not None and low is not None and 0 < low <= f.close <= high:
        f.close_pos = (f.close - low) / (high - low) if high > low else 1.0
    return f


# ---------- 策略 ----------

Rule = Callable[[Features, dict], "tuple[float, str] | None"]


@dataclass(frozen=True)
class Strategy:
    name: str
    label: str
    description: str
    regimes: tuple[str, ...]        # 适配的大盘环境
    params: dict[str, float]
    rule: Rule


def _clamp(score: float) -> float:
    return round(max(0.0, min(100.0, score)), 1)


def _merged_score(scores: list[float]) -> float:
    """同时被多个策略选中：取最高分，每多一个策略加 5 分。"""
    return _clamp(max(scores) + MULTI_STRATEGY_BONUS * (len(scores) - 1))


def _volume_breakout(f: Features, p: dict) -> tuple[float, str] | None:
    if f.is_limit_up or None in (f.high_20, f.range_20, f.vol_ratio, f.ma20):
        return None
    if not (f.change_pct >= p["change_min"] and f.amount >= p["amount_min"] and f.vol_ratio >= p["vol_ratio_min"]
            and f.close >= f.high_20 and f.close >= f.ma20 and f.range_20 <= p["range_max"]):
        return None
    if f.close_pos is not None and f.close_pos < p["close_pos_min"]:
        return None
    score = 50 + min(f.vol_ratio, 6) * 4 + min(f.change_pct, 9) * 2 + (f.close_pos or 0.5) * 10 - max(f.range_20 - 15, 0) * 0.5
    return _clamp(score), f"放量 {f.vol_ratio:.1f} 倍突破 20 日高点 {f.high_20:.2f}（前 20 日振幅 {f.range_20:.0f}%）"


def _strong_close(f: Features, p: dict) -> tuple[float, str] | None:
    if f.is_limit_up or f.vol_ratio is None or f.close_pos is None:
        return None
    if not (f.change_pct >= p["change_min"] and f.close_pos >= p["close_pos_min"]
            and f.vol_ratio >= p["vol_ratio_min"] and f.amount >= p["amount_min"]):
        return None
    if f.turnover is not None and not (p["turnover_min"] <= f.turnover <= p["turnover_max"]):
        return None
    if f.ma5 is not None and f.close < f.ma5:
        return None
    score = 40 + f.change_pct * 3 + f.close_pos * 15 + min(f.vol_ratio, 5) * 3
    return _clamp(score), f"涨 {f.change_pct:.1f}% 收在全天高位（{f.close_pos:.0%}），量比 {f.vol_ratio:.1f}"


def _dragon_pullback(f: Features, p: dict) -> tuple[float, str] | None:
    if f.limit_ups_15 < p["limit_ups_min"] or f.pullback_15 is None or f.vol_ratio is None:
        return None
    if not (p["pullback_min"] <= f.pullback_15 <= p["pullback_max"] and f.vol_ratio <= p["vol_ratio_max"]
            and f.change_pct >= p["change_min"]):
        return None
    if f.ma20 is not None and f.close < f.ma20 * p["ma20_floor"]:
        return None
    support = next((label for label, ma in (("MA10", f.ma10), ("MA20", f.ma20)) if ma and abs(f.close / ma - 1) <= 0.03), "")
    score = 45 + f.limit_ups_15 * 8 + (p["vol_ratio_max"] - f.vol_ratio) * 15 + (8 if support else 0) - abs(f.pullback_15 + 15) * 0.8
    reason = f"近 15 日 {f.limit_ups_15} 次涨停，自 15 日高点回撤 {abs(f.pullback_15):.0f}%，缩量（量比 {f.vol_ratio:.1f}）"
    return _clamp(score), reason + (f"回踩 {support}" if support else "")


def _theme_follow(f: Features, p: dict) -> tuple[float, str] | None:
    if f.is_limit_up or not f.themes:
        return None
    if not (f.change_pct >= p["change_min"] and f.amount >= p["amount_min"]):
        return None
    if f.ma5 is not None and f.close < f.ma5:
        return None
    theme, heat = max(f.themes.items(), key=lambda kv: kv[1])
    score = 45 + heat * 0.3 + min(f.change_pct, 9) * 2 + min(len(f.themes) - 1, 2) * 3
    return _clamp(score), f"主线「{theme}」（热度 {heat:.0f}）的辨识度个股（近期因该方向涨停过），今日 {f.change_pct:+.1f}%"


def _trend_pullback(f: Features, p: dict) -> tuple[float, str] | None:
    if None in (f.ma5, f.ma10, f.ma20, f.vol_ratio, f.ret_20) or not (f.ma5 >= f.ma10 >= f.ma20):
        return None
    dist = (f.close / f.ma10 - 1) * 100
    if not (p["dist_min"] <= dist <= p["dist_max"] and f.vol_ratio <= p["vol_ratio_max"]
            and p["ret20_min"] <= f.ret_20 <= p["ret20_max"] and abs(f.change_pct) <= p["change_abs_max"]
            and f.amount >= p["amount_min"]):
        return None
    score = 50 + min(f.ret_20, 30) * 0.6 + (p["vol_ratio_max"] - f.vol_ratio) * 20 - abs(dist) * 3
    return _clamp(score), f"均线多头，缩量（量比 {f.vol_ratio:.1f}）回踩 MA10（偏离 {dist:+.1f}%），20 日涨幅 {f.ret_20:.0f}%"


def _oversold_rebound(f: Features, p: dict) -> tuple[float, str] | None:
    if f.is_limit_up or f.ret_20 is None or f.vol_ratio is None:
        return None
    if not (f.ret_20 <= p["ret20_max"] and f.change_pct >= p["change_min"] and f.vol_ratio >= p["vol_ratio_min"]
            and f.amount >= p["amount_min"]):
        return None
    if f.close_pos is not None and f.close_pos < p["close_pos_min"]:
        return None
    score = 45 + min(abs(f.ret_20), 40) * 0.6 + min(f.change_pct, 9) * 2 + min(f.vol_ratio, 4) * 3
    return _clamp(score), f"20 日跌 {abs(f.ret_20):.0f}% 后放量（量比 {f.vol_ratio:.1f}）反弹 {f.change_pct:+.1f}%"


STRATEGIES: tuple[Strategy, ...] = (
    Strategy("volume_breakout", "放量突破", "放量突破 20 日平台高点、站上 MA20、收盘强势，尚未涨停", ("进攻", "均衡"),
             {"change_min": 3, "amount_min": 1e8, "vol_ratio_min": 2.0, "range_max": 30, "close_pos_min": 0.7}, _volume_breakout),
    Strategy("strong_close", "强势未板", "大涨未封板且收在全天高位、放量，次日冲板候选", ("进攻", "均衡"),
             {"change_min": 6, "close_pos_min": 0.9, "vol_ratio_min": 1.5, "amount_min": 2e8, "turnover_min": 3, "turnover_max": 25},
             _strong_close),
    Strategy("dragon_pullback", "龙回头", "近 15 日多次涨停的强势股缩量回调到均线附近", ("进攻", "均衡"),
             {"limit_ups_min": 2, "pullback_min": -25, "pullback_max": -8, "vol_ratio_max": 0.8, "change_min": -4, "ma20_floor": 0.97},
             _dragon_pullback),
    Strategy("theme_follow", "主线补涨", "当前主线里近期涨停过、今天跟涨但还没涨停的辨识度个股", ("进攻", "均衡"),
             {"change_min": 3, "amount_min": 1e8}, _theme_follow),
    Strategy("trend_pullback", "缩量回踩", "均线多头排列的趋势股缩量回踩 MA10", ("均衡", "防守"),
             {"dist_min": -2, "dist_max": 3, "vol_ratio_max": 0.8, "ret20_min": 5, "ret20_max": 40, "change_abs_max": 3, "amount_min": 8e7},
             _trend_pullback),
    Strategy("oversold_rebound", "超跌反弹", "20 日深跌后放量反弹、收盘偏强", ("防守", "冰点"),
             {"ret20_max": -20, "change_min": 3, "vol_ratio_min": 1.5, "amount_min": 5e7, "close_pos_min": 0.6}, _oversold_rebound),
)


# ---------- 结果 ----------

@dataclass
class Pick:
    code: str
    name: str
    strategies: list[str]          # 策略标识
    labels: list[str]              # 策略名称
    score: float                   # 最高策略分 + 多策略加分
    reasons: list[str]
    scores: list[float]            # 各策略自己的得分（与 strategies 一一对应，降序）
    close: float
    change_pct: float
    amount_yi: float
    fits_regime: bool
    event_risks: list[str] = field(default_factory=list)
    screen_score: float = 0
    factor_scores: dict = field(default_factory=dict)
    factor_coverage: float = 0
    data_quality: dict = field(default_factory=dict)
    financial_status: str = "missing"
    industry: str = ""
    themes: list[str] = field(default_factory=list)
    risk_penalty: float = 0
    portfolio_penalty: float = 0
    risk_flags: list[str] = field(default_factory=list)
    risk_level: str = "low"
    excluded_by_risk: bool = False
    llm_score: float | None = None
    llm_reason: str = ""
    context_pack: dict = field(default_factory=dict)
    post_analysis: dict = field(default_factory=dict)
    why_selected: list[dict] = field(default_factory=list)
    why_now: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScreenResult:
    trade_date: str = ""
    regime: str = ""
    picks: list[Pick] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)   # 生效的策略权重（未回测时为空）
    status: str = "success"
    pipeline: dict = field(default_factory=dict)
    excluded: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "picks": [p.to_dict() for p in self.picks]}

    def prompt_lines(self, limit: int = 20) -> list[str]:
        return [
            f"{p.name}({p.code}) 策略={'+'.join(p.labels)}{'' if p.fits_regime else '（与当前大盘环境不匹配）'} "
            f"得分={p.score:.0f} 涨幅={p.change_pct:+.1f}% 成交={p.amount_yi:.1f}亿 理由={'；'.join(p.reasons)}"
            for p in self.picks[:limit]
        ]


# ---------- 选股 ----------

class StrategyScreener:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        cfg = self.config.get("screening") or {}
        self.max_per_strategy = int(cfg.get("max_per_strategy", 10))
        self.max_total = int(cfg.get("max_total", 30))
        self.adaptive_weights = bool(cfg.get("adaptive_strategy_weights", True))
        self.minimum_universe = int(cfg.get("minimum_universe", 0))
        self.financial_cache_max_age_days = int(cfg.get("financial_cache_max_age_days", 30))
        self.pipeline_cfg = cfg.get("pipeline") or {}
        from src.strategy.screening_pipeline import validate_pipeline
        validate_pipeline(self.pipeline_cfg)
        self.profiles = []
        self.strategies = self._load_strategies(cfg.get("strategies") or {})
        self.rule_definitions = []
        if cfg.get("rules_file"):
            from src.strategy.screening_rules import load_rules, matches
            self.rule_definitions = load_rules(cfg["rules_file"])
            for rule in self.rule_definitions:
                if rule["name"] in {strategy.name for strategy in STRATEGIES}:
                    raise ValueError("YAML 策略不能覆盖内置策略标识")
                def evaluate(features, params, definition=rule):
                    return (definition.get("score", 60), definition.get("label", definition["name"]) + "条件满足") if matches(features, definition["conditions"]) else None
                self.strategies.append(Strategy(rule["name"], rule.get("label", rule["name"]), rule.get("description", "YAML 规则"), tuple(rule.get("regimes") or ("进攻", "均衡", "防守", "冰点")), {}, evaluate))
        self.pool_cfg = load_config(STOCK_POOL_CONFIG_PATH) or {}
        if cfg.get("profiles_file"):
            from src.strategy.screening_pipeline import load_profiles, profile_rule
            self.profiles = load_profiles(cfg["profiles_file"])
            for definition in self.profiles:
                if definition["name"] in {s.name for s in self.strategies}:
                    raise ValueError("多因子策略标识不能覆盖现有策略")
                self.strategies.append(Strategy(definition["name"], definition.get("label", definition["name"]),
                    definition.get("description", "多因子"), tuple(definition.get("regimes", ["进攻", "均衡", "防守", "冰点"])), {},
                    lambda f, p, definition=definition: profile_rule(f, definition)))

    @staticmethod
    def _load_strategies(overrides: dict) -> list[Strategy]:
        if not isinstance(overrides, dict) or set(overrides) - {s.name for s in STRATEGIES}:
            raise ValueError("screening.strategies 包含未知策略或格式错误")
        result = []
        for s in STRATEGIES:
            custom = overrides.get(s.name) or {}
            if not isinstance(custom, dict) or set(custom) - {*s.params, "enabled"}:
                raise ValueError(f"{s.name} 参数格式错误或包含未知参数")
            if not isinstance(custom.get("enabled", True), bool):
                raise ValueError(f"{s.name}.enabled 必须为布尔值")
            if any(finite_number(v) is None or not isinstance(v, (int, float)) or isinstance(v, bool)
                   for k, v in custom.items() if k in s.params):
                raise ValueError(f"{s.name} 参数必须为有限数值")
            if custom.get("enabled", True) is False:
                continue
            params = {**s.params, **{k: float(v) for k, v in custom.items() if k in s.params}}
            for key, value in params.items():
                if key.endswith("_min") and key[:-4] + "_max" in params and value > params[key[:-4] + "_max"]:
                    raise ValueError(f"{s.name}.{key} 不能大于上限")
                if (key.startswith(("amount_", "vol_ratio", "turnover_", "limit_ups_", "range_", "ma20_floor")) and value < 0
                        or key.startswith("close_pos_") and not 0 <= value <= 1):
                    raise ValueError(f"{s.name}.{key} 超出有效范围")
            result.append(Strategy(s.name, s.label, s.description, s.regimes, params, s.rule))
        return result

    def strategy_signature(self) -> str:
        definition = {"strategies": [{"name": s.name, "params": s.params, "regimes": s.regimes} for s in self.strategies],
                      "rules": self.rule_definitions, "pool": self.pool_cfg, "max_per_strategy": self.max_per_strategy,
                      "financial_cache_max_age_days": self.financial_cache_max_age_days,
                      "profiles": self.profiles, "pipeline": self.pipeline_cfg}
        return hashlib.sha256(json.dumps(definition, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def run(self, trade_date: str | None = None, save: bool = True, point_in_time: bool = False) -> ScreenResult:
        """point_in_time=True 用于历史回测：只用 trade_date 当天及以前的数据，策略权重不生效。"""
        trading_calendar.load(self.db_path, refresh=False)
        from src.services.market_phase import current_phase
        expected = current_phase().get("effective_daily_bar_date")
        historical = point_in_time or bool(trade_date and expected and trade_date < expected)
        if historical:
            point_in_time = True
        with get_db_session(self.db_path) as session:
            trade_date = trade_date or self._latest_trade_date(session)
            if not trade_date:
                return ScreenResult(notes=["没有行情数据，请先采集"], status="partial")
            result = ScreenResult(trade_date=trade_date)
            from src.services.data_freshness import daily_quality
            date_quality = daily_quality(trade_date, historical=historical)
            snapshot = self._snapshot(session, trade_date)
            pool = [r for r in snapshot.values() if self._in_pool(r)]
            candidates = [r for r in pool if (r.amount or 0) >= PRE_FILTER_AMOUNT]
            bars = self._history(session, trade_date, [r.code for r in candidates])
            if not historical:
                for row in snapshot.values():
                    quality = daily_quality(row.trade_date, getattr(row, "updated_at", None))
                    if quality["status"] != "available":
                        date_quality = quality
                        break
            stock_themes = self._stock_themes(session, trade_date)
            industries = {}
            for row in session.query(LimitUpStock).filter(LimitUpStock.trade_date <= trade_date).order_by(LimitUpStock.trade_date.desc()).limit(20000):
                code = bare_code(row.code)
                if row.sector and code not in industries and (not point_in_time or row.created_at < datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=1)):
                    industries[code] = row.sector
            fundamentals = self._financial_payloads(session, [bare_code(r.code) for r in candidates], trade_date, point_in_time)
        main_lines = self._main_lines(trade_date)
        result.regime = self._regime(trade_date, point_in_time)
        result.weights = {} if point_in_time else self.strategy_weights()
        result.pipeline = {"version": "screening-pipeline-v1", "mode": "point_in_time" if point_in_time else "live"}
        result.pipeline["daily_quality"] = date_quality
        if date_quality["status"] != "available":
            result.notes.append("行情不是当前完整交易日，禁止联网补数、模型重排及覆盖成功批次：" + "、".join(date_quality["limitations"]))
        if not point_in_time and date_quality["status"] == "available" and self.pipeline_cfg.get("financial_enrichment", False):
            self._enrich_financials(candidates, fundamentals, result)

        features = []
        for row in candidates:
            code = bare_code(row.code)
            f = compute_features(code, row.name or "", bars.get(code) or [row])
            if f is None:
                continue
            f.pe, f.pb = finite_number(getattr(row, "pe", None)), finite_number(getattr(row, "pb", None))
            f.circ_mv = finite_number(getattr(row, "circ_mv", None))
            self._financial_features(f, trade_date, fundamentals.get(code))
            f.fundamentals = fundamentals.get(code) or {}
            f.industry = industries.get(code, "")
            f.themes = {t: main_lines[t] for t in stock_themes.get(code, ()) if t in main_lines}
            from src.strategy.screening_pipeline import quality
            f.data_quality = quality(bars.get(code) or [row], f, fundamentals.get(code))
            features.append(f)

        with_history = sum(1 for f in features if f.bars >= 21)
        result.stats = {"universe": len(snapshot), "pool": len(pool), "candidates": len(candidates),
                        "valid_features": len(features), "with_history": with_history, "financial_cache": len(fundamentals)}
        result.stats["financial_coverage_pct"] = round(len(fundamentals) / max(1, len(candidates)) * 100)
        result.stats["quality_qualified"] = sum(f.data_quality["score"] >= 75 for f in features)
        if len(snapshot) < MIN_UNIVERSE:
            result.notes.append(f"{trade_date} 只有 {len(snapshot)} 只股票的行情，不是全市场，选股结果不完整")
        history_complete = not candidates or with_history >= len(candidates) * MIN_HISTORY_COVERAGE
        if not history_complete:
            start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=HISTORY_CALENDAR_DAYS)).strftime("%Y-%m-%d")
            result.notes.append(f"只有 {with_history}/{len(candidates)} 只股票有 20 日以上有效日线，依赖历史的策略会漏选；"
                                f"可运行 python scripts/fetch_history.py --mode daily --start-date {start} 补齐")
        if result.regime in ("", "未知"):
            result.notes.append("大盘环境未知，所有策略按适配处理")
        complete = len(snapshot) >= self.minimum_universe and history_complete and date_quality["status"] == "available"
        result.picks = self._rank(features, result, result.weights, point_in_time=point_in_time, allow_llm=complete)
        result.status = "success" if complete else "partial"
        if not complete:
            result.notes.append("行情覆盖或历史质量不足，保留上次成功选股结果；本次候选不进入 AI 预测")
        if complete and not point_in_time and self.pipeline_cfg.get("post_analysis_top_k", 0):
            self._post_analysis(result)
        if save:
            if complete:
                self._save(result)
            self._record_run(result, result.status)
        logger.info(f"策略选股 {trade_date}：全市场 {len(snapshot)} 只 → 股票池 {len(pool)} → 入选 {len(result.picks)} 只")
        return result

    def _rank(self, features: list[Features], result: ScreenResult, weights: dict[str, float] | None = None, *, point_in_time: bool = False, allow_llm: bool = True) -> list[Pick]:
        hits: dict[str, list[tuple[Strategy, float, str]]] = defaultdict(list)
        by_code = {f.code: f for f in features}
        for strategy in self.strategies:
            weight = (weights or {}).get(strategy.name, 1.0)
            matched = []
            for f in features:
                outcome = strategy.rule(f, strategy.params)
                if outcome and finite_number(outcome[0] * weight) is not None:
                    matched.append((f.code, _clamp(outcome[0] * weight), outcome[1]))
            matched.sort(key=lambda m: -m[1])
            result.stats[strategy.name] = len(matched)
            for code, score, reason in matched[: self.max_per_strategy]:
                hits[code].append((strategy, score, reason))

        unknown = result.regime in ("", "未知")
        picks = []
        for code, items in hits.items():
            f = by_code[code]
            items.sort(key=lambda it: -it[1])
            fits = unknown or any(result.regime in s.regimes for s, _, _ in items)
            picks.append(Pick(
                code=code, name=f.name, strategies=[s.name for s, _, _ in items], labels=[s.label for s, _, _ in items],
                score=_merged_score([sc for _, sc, _ in items]), reasons=[r for _, _, r in items],
                scores=[sc for _, sc, _ in items],
                close=f.close, change_pct=round(f.change_pct, 2), amount_yi=round(f.amount / 1e8, 2), fits_regime=fits,
                data_quality=f.data_quality, industry=f.industry, themes=list(f.themes),
                financial_status="available" if f.roe is not None else "missing",
            ))
        from src.database.models import FinanceNews, StockFundFlow
        from src.collectors.stock_news import classify_notice
        cutoff = datetime.strptime(result.trade_date, "%Y-%m-%d") + timedelta(days=1)
        with get_db_session(self.db_path) as session:
            query = session.query(FinanceNews).filter(FinanceNews.news_time >= cutoff - timedelta(days=7), FinanceNews.news_time < cutoff)
            if point_in_time:
                query = query.filter(FinanceNews.created_at < cutoff)
            news = query.all()
            flows = {}
            for flow in session.query(StockFundFlow).filter(StockFundFlow.trade_date == result.trade_date):
                if not point_in_time or flow.updated_at < cutoff:
                    flows[bare_code(flow.code)] = {"net_inflow": flow.net_inflow, "net_ratio": flow.net_ratio, "source": flow.source, "trade_date": flow.trade_date, "fetched_at": flow.updated_at.isoformat() if flow.updated_at else None}
        for pick in picks:
            related = [item for item in news if pick.code in item.title or (pick.name and pick.name in item.title)][:10]
            pick.event_risks = [item.title for item in related if classify_notice(item.title)[0]][:5]
            from src.services.research_artifact import build_context_pack
            pick.context_pack = build_context_pack({"code": pick.code, "name": pick.name,
                "phase": {"trade_date": result.trade_date, "point_in_time": point_in_time}, "data_quality": {**pick.data_quality, "bar_count": by_code[pick.code].bars},
                "quote": {"close": pick.close, "trade_date": result.trade_date, "source": pick.data_quality.get("source"), "fetched_at": pick.data_quality.get("fetched_at")},
                "technical": asdict(by_code[pick.code]), "fundamentals": by_code[pick.code].fundamentals,
                "flow": flows.get(pick.code), "news_evidence": [{"id": n.id, "title": n.title, "source": n.source, "news_time": str(n.news_time or ""), "collected_at": str(n.collected_at or ""), "url": n.url} for n in related]})
            if pick.event_risks:
                pick.reasons.append("事件风险待复核：" + "；".join(pick.event_risks))
        picks.sort(key=lambda p: (not p.fits_regime, -p.score, p.code))
        from src.strategy.screening_explanations import explanations
        for pick in picks:
            pick.why_selected, pick.why_now = explanations(pick, by_code[pick.code], result.trade_date)
        from src.strategy.screening_pipeline import factor_scores, weighted_score, apply_risk, diversify, rerank
        for p in picks:
            p.factor_scores = factor_scores(by_code[p.code])
            factor, p.factor_coverage = weighted_score(p.factor_scores, {key: 1 for key in p.factor_scores})
            if self.pipeline_cfg.get("enabled", False):
                p.score = _clamp(.5 * p.score + .5 * factor)
            p.screen_score = p.score
        if self.pipeline_cfg.get("enabled", False):
            if self.pipeline_cfg.get("llm_rerank", False) and not point_in_time and allow_llm:
                self._enrich_candidate_context(picks, result)
            kept = []
            for p in picks:
                if apply_risk(p, by_code[p.code], self.pipeline_cfg):
                    kept.append(p)
                else:
                    p.why_selected, p.why_now = explanations(p, by_code[p.code], result.trade_date)
                    result.excluded.append(p.to_dict())
            picks = sorted(kept, key=lambda p: (not p.fits_regime, -p.score, p.code))
            if self.pipeline_cfg.get("llm_rerank", False) and not point_in_time and allow_llm:
                from src.analyzers.llm_client import LLMClient
                picks, result.pipeline["llm_rerank"] = rerank(picks, self.pipeline_cfg, LLMClient(self.config.get("llm") or {}), seconds=self.pipeline_cfg.get("llm_timeout_seconds", 40))
            else:
                result.pipeline["llm_rerank"] = {"status": "skipped", "reason": "历史回测禁止使用实时模型" if point_in_time else "未启用"}
            picks = diversify(picks, self.pipeline_cfg)
        for pick in picks:
            pick.why_selected, pick.why_now = explanations(pick, by_code[pick.code], result.trade_date)
        return picks[: self.max_total]

    def _enrich_candidate_context(self, picks: list[Pick], result: ScreenResult):
        """在固定预算内补候选公告和新闻，先做风险复核再交给模型。"""
        import time
        from src.collectors.request_budget import bounded_call
        from src.collectors.stock_news import get_stock_news, classify_notice
        from src.schemas.research import ContextBlock, ContextItem
        from src.utils.redaction import redact_text
        expires = time.monotonic() + self.pipeline_cfg.get("candidate_context_timeout_seconds", 30)
        counts = {"available": 0, "failed": 0, "skipped": 0}
        for pick in picks[:int(self.pipeline_cfg.get("llm_top_k", 15))]:
            remaining = expires - time.monotonic()
            if remaining <= 0:
                counts["skipped"] += 1
                continue
            try:
                data = bounded_call(lambda code=pick.code: get_stock_news(code, include_status=True), min(remaining, 15))
                for key in ("news", "notices"):
                    values = data.get(key) or []
                    previous = pick.context_pack["blocks"].get(key, {}).get("items", {}).get("evidence", {}).get("value") or []
                    values = [*previous, *values]
                    failed = (data.get("states") or {}).get(key) == "fetch_failed"
                    status = "partial" if failed and values else "fetch_failed" if failed else "available"
                    block = ContextBlock(status=status, source="本地资讯/东方财富", items={"evidence": ContextItem(status=status, value=values, source="本地资讯/东方财富")})
                    pick.context_pack["blocks"][key] = block.model_dump(mode="json")
                pick.event_risks = list(dict.fromkeys([*pick.event_risks, *[n["title"] for n in data.get("notices", []) if classify_notice(n.get("title", ""))[0]]]))[:10]
                counts["failed" if "fetch_failed" in (data.get("states") or {}).values() else "available"] += 1
            except Exception as error:
                counts["failed"] += 1
                pick.context_pack["blocks"]["notices"] = ContextBlock(status="fetch_failed", limitations=[redact_text(error, 200)]).model_dump(mode="json")
        result.pipeline["candidate_context"] = counts

    def _enrich_financials(self, candidates, payloads: dict, result: ScreenResult):
        import time
        from src.collectors.quarterly_fundamentals import QuarterlyFundamentals
        from src.collectors.request_budget import POLICY, RequestPolicy
        expires = time.monotonic() + self.pipeline_cfg.get("financial_timeout_seconds", 60)
        ordered = sorted(candidates, key=lambda row: -(finite_number(row.amount) or 0))
        attempted = 0
        for row in ordered[:int(self.pipeline_cfg.get("financial_candidates", 40))]:
            code = bare_code(row.code)
            if code in payloads:
                continue
            remaining = expires - time.monotonic()
            if remaining <= 0:
                break
            cfg = {**self.config, "data_sources": {**(self.config.get("data_sources") or {}), "stage_timeout_seconds": remaining}}
            previous = POLICY.get()
            try:
                payload = QuarterlyFundamentals(cfg).get(code)
                attempted += 1
                if payload.get("status") in {"available", "partial"}:
                    payloads[code] = payload
            except Exception as error:
                logger.warning("候选财务补数失败 {}: {}", code, error)
            finally:
                POLICY.set(previous)
        result.pipeline["financial_enrichment"] = {"attempted": attempted, "covered": len(payloads), "budget_exhausted": time.monotonic() >= expires}

    def _post_analysis(self, result: ScreenResult):
        import time
        from src.services.analysis_process import isolated_result
        expires = time.monotonic() + self.pipeline_cfg.get("post_analysis_timeout_seconds", 90)
        for pick in result.picks[:int(self.pipeline_cfg.get("post_analysis_top_k", 3))]:
            if time.monotonic() >= expires:
                pick.post_analysis = {"status": "skipped", "reason": "复核预算耗尽"}
                continue
            try:
                cfg = {**self.config, "diagnosis": {**(self.config.get("diagnosis") or {}), "timeout_seconds": min(60, expires - time.monotonic())}}
                report = isolated_result("diagnosis", cfg, {"code": pick.code, "force": False})
                if report.get("error") or not report.get("diagnosis_id"):
                    raise RuntimeError(report.get("error") or "复核未生成有效报告")
                pick.post_analysis = {"status": "available", "report_id": report.get("diagnosis_id"), "action": report.get("action"), "score": report.get("score")}
            except Exception as error:
                from src.utils.redaction import redact_text
                pick.post_analysis = {"status": "fetch_failed", "reason": redact_text(error, 200)}

    def _financial_payloads(self, session, codes: list[str], trade_date: str, point_in_time: bool) -> dict[str, dict]:
        """批量读取缓存；个别损坏缓存不能拖垮全市场筛选。"""
        from src.database.models import ResearchCache
        keys = ["fundamentals:" + code for code in codes]
        cutoff = datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=1)
        since = (cutoff if point_in_time else datetime.now()) - timedelta(days=self.financial_cache_max_age_days)
        result = {}
        if point_in_time:
            from src.database.models import FinancialSnapshot
            for i in range(0, len(codes), CHUNK):
                snapshots = session.query(FinancialSnapshot).filter(FinancialSnapshot.code.in_(codes[i:i + CHUNK]),
                    FinancialSnapshot.collected_at >= since, FinancialSnapshot.collected_at < cutoff).order_by(FinancialSnapshot.collected_at.desc(), FinancialSnapshot.id.desc()).all()
                for row in snapshots:
                    if row.code in result:
                        continue
                    try:
                        payload = json.loads(row.payload_json)
                        if isinstance(payload, dict) and payload.get("status") in {"available", "partial"}:
                            result[row.code] = payload
                    except (ValueError, TypeError):
                        continue
        for i in range(0, len(keys), CHUNK):
            rows = session.query(ResearchCache).filter(ResearchCache.key.in_(keys[i:i + CHUNK]), ResearchCache.updated_at >= since).all()
            for row in rows:
                if point_in_time and row.updated_at >= cutoff:
                    continue
                try:
                    payload = json.loads(row.payload_json)
                except (ValueError, TypeError):
                    continue
                if isinstance(payload, dict) and payload.get("status") not in {"stale", "missing", "fetch_failed", "not_supported"}:
                    result.setdefault(row.key.split(":", 1)[1], payload)
        return result

    @staticmethod
    def _financial_features(features: Features, trade_date: str, payload: dict | None):
        if not payload:
            return
        reports = payload.get("reports")
        if not isinstance(reports, list):
            return
        reports = [report for report in reports if isinstance(report, dict) and iso_date(report.get("report_date"))
                   and report["report_date"] <= trade_date]
        if not reports:
            return
        reports.sort(key=lambda report: report["report_date"], reverse=True)
        for key in ("roe", "profit_yoy", "revenue_yoy"):
            setattr(features, key, finite_number(reports[0].get(key)))
        dividend = payload.get("dividend")
        events = dividend.get("events", []) if isinstance(dividend, dict) else []
        if not isinstance(events, list):
            return
        since = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
        dividends = [finite_number(event.get("cash_per_share")) for event in events if isinstance(event, dict)
                     and iso_date(event.get("ex_dividend_date")) and since <= event["ex_dividend_date"] <= trade_date]
        dividends = [value for value in dividends if value is not None and value >= 0]
        if dividends and features.close > 0:
            features.dividend_yield = sum(dividends) / features.close * 100

    def _record_run(self, result: ScreenResult, status: str):
        from src.database.models import ScreeningRun
        with get_db_session(self.db_path) as session:
            session.add(ScreeningRun(trade_date=result.trade_date, status=status, result_json=json.dumps(result.to_dict(), ensure_ascii=False)))

    def runs(self, limit: int = 20) -> list[dict]:
        from src.database.models import ScreeningRun
        with get_db_session(self.db_path) as session:
            return [{"id": row.id, "trade_date": row.trade_date, "status": row.status, "created_at": row.created_at.isoformat(), **json.loads(row.result_json)} for row in session.query(ScreeningRun).order_by(ScreeningRun.id.desc()).limit(limit)]

    # ---- 数据 ----

    @staticmethod
    def _latest_trade_date(session) -> str:
        recent = [d for (d,) in session.query(StockDaily.trade_date).filter(
            StockDaily.trade_date >= (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        ).distinct().order_by(StockDaily.trade_date.desc()).limit(10).all()]
        days = trading_calendar.trade_days_only(recent)
        return days[0] if days else (session.query(func.max(StockDaily.trade_date)).scalar() or "")

    @staticmethod
    def _snapshot(session, trade_date: str) -> dict[str, Any]:
        rows = (
            session.query(StockDaily.code, StockDaily.name, StockDaily.trade_date, StockDaily.open, StockDaily.high,
                          StockDaily.low, StockDaily.close, StockDaily.volume, StockDaily.amount, StockDaily.change_pct,
                          StockDaily.turnover, StockDaily.circ_mv, StockDaily.pe, StockDaily.pb,
                          StockDaily.price_adjustment, StockDaily.source, StockDaily.updated_at, StockDaily.id)
            .filter(StockDaily.trade_date == trade_date, StockDaily.close > 0)
            .all()
        )
        result = {}
        for row in rows:
            code = equity_code(row.code)
            close, change, amount = finite_number(row.close), finite_number(row.change_pct), finite_number(row.amount)
            if code is None or close is None or close <= 0 or change is None or amount is None or amount < 0:
                continue
            if code not in result or row_priority(row) > row_priority(result[code]):
                result[code] = row
        return result

    def _in_pool(self, row) -> bool:
        blacklist = self.pool_cfg.get("blacklist") or {}
        name = row.name or ""
        if any(str(kw) in name for kw in blacklist.get("name_keywords") or [] if kw):
            return False
        if bare_code(row.code) in {bare_code(str(c)) for c in blacklist.get("codes") or []}:
            return False
        price = self.pool_cfg.get("price") or {}
        if not (price.get("min", 0) <= row.close <= price.get("max", 1e9)):
            return False
        cap = self.pool_cfg.get("market_cap") or {}
        if row.circ_mv and not (cap.get("min", 0) * 1e8 <= row.circ_mv <= cap.get("max", 1e9) * 1e8):
            return False
        return True

    @staticmethod
    def _history(session, trade_date: str, codes: list[str]) -> dict[str, list]:
        start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=HISTORY_CALENDAR_DAYS)).strftime("%Y-%m-%d")
        variants = [v for c in codes for v in code_candidates(c)]
        by_code: dict[str, dict[str, Any]] = defaultdict(dict)
        for i in range(0, len(variants), CHUNK):
            rows = (
                session.query(StockDaily.code, StockDaily.trade_date, StockDaily.open, StockDaily.high, StockDaily.low,
                              StockDaily.close, StockDaily.volume, StockDaily.amount, StockDaily.change_pct, StockDaily.turnover,
                              StockDaily.price_adjustment, StockDaily.source, StockDaily.updated_at, StockDaily.id)
                .filter(StockDaily.code.in_(variants[i:i + CHUNK]), StockDaily.trade_date >= start,
                        StockDaily.trade_date <= trade_date)
                .all()
            )
            for r in rows:
                code = equity_code(r.code)
                if code and (r.trade_date not in by_code[code] or row_priority(r) > row_priority(by_code[code][r.trade_date])):
                    by_code[code][r.trade_date] = r
        result = {}
        for code, days in by_code.items():
            dates = sorted(trading_calendar.trade_days_only(days))[-HISTORY_BARS:]
            if dates and dates[-1] == trade_date:
                result[code] = [days[d] for d in dates]
        return result

    @staticmethod
    def _stock_themes(session, trade_date: str) -> dict[str, set[str]]:
        """近 60 天涨停过的股票 → 当时的行业和题材。"""
        from src.analyzers.theme_tracker import UNKNOWN_SECTORS, is_generic_concept
        from src.collectors.limit_up_reasons import split_concepts

        start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=THEME_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        rows = (
            session.query(LimitUpStock.code, LimitUpStock.sector, LimitUpStock.concepts)
            .filter(LimitUpStock.trade_date >= start, LimitUpStock.trade_date <= trade_date)
            .all()
        )
        themes: dict[str, set[str]] = defaultdict(set)
        for code, sector, concepts in rows:
            tags = {(sector or "").strip(), *(c for c in split_concepts(concepts) if not is_generic_concept(c))}
            themes[bare_code(code)].update(t for t in tags if t not in UNKNOWN_SECTORS)
        return themes

    def _main_lines(self, trade_date: str) -> dict[str, float]:
        try:
            from src.analyzers.theme_tracker import ThemeTracker

            tracker = ThemeTracker(self.config)
            lines = {}
            for dimension in ("industry", "concept"):
                for t in tracker.main_lines(tracker.analyze(trade_date=trade_date, dimension=dimension), top=MAIN_LINE_TOP):
                    lines[t.name] = t.heat
            return lines
        except Exception as e:
            logger.debug(f"策略选股读取主线失败: {e}")
            return {}

    def _regime(self, trade_date: str, point_in_time: bool = False) -> str:
        try:
            from src.analyzers.market_regime import MarketRegimeAnalyzer

            # 回测时不能用进程内的实时指数涨跌（那是今天的数据）
            return MarketRegimeAnalyzer(self.config).analyze(trade_date=trade_date, overview={} if point_in_time else None).regime
        except Exception as e:
            logger.debug(f"策略选股评估大盘环境失败: {e}")
            return ""

    def _save(self, result: ScreenResult) -> None:
        regimes = {strategy.name: strategy.regimes for strategy in self.strategies}
        with get_db_session(self.db_path) as session:
            session.query(StrategyPick).filter(StrategyPick.trade_date == result.trade_date).delete()
            for p in result.picks:
                for strategy, score, reason in zip(p.strategies, p.scores, p.reasons):
                    session.add(StrategyPick(
                        trade_date=result.trade_date, strategy=strategy, code=p.code, name=p.name, score=score,
                        reason=reason, close=p.close, change_pct=p.change_pct,
                        fits_regime=result.regime in ("", "未知") or result.regime in regimes[strategy],
                        metadata_json=json.dumps(p.to_dict(), ensure_ascii=False),
                    ))

    def strategy_weights(self) -> dict[str, float]:
        """最近 30 天内一次历史回测得出的策略权重；没有回测或关闭 adaptive_strategy_weights 时为空（都按 1.0）。"""
        if not self.adaptive_weights:
            return {}
        try:
            from src.database.models import StrategyBacktest

            since = datetime.now() - timedelta(days=WEIGHTS_MAX_AGE_DAYS)
            with get_db_session(self.db_path) as session:
                rows = (
                    session.query(StrategyBacktest.result_json).filter(StrategyBacktest.created_at >= since,
                        StrategyBacktest.end_date >= since.strftime("%Y-%m-%d"),
                        StrategyBacktest.end_date <= datetime.now().strftime("%Y-%m-%d"))
                    .order_by(StrategyBacktest.created_at.desc(), StrategyBacktest.id.desc()).limit(20).all()
                )
            signature = self.strategy_signature()
            names = {strategy.name for strategy in self.strategies}
            for row in rows:
                try:
                    report = json.loads(row[0])
                except (ValueError, TypeError):
                    continue
                if (not isinstance(report, dict) or report.get("engine_version") != BACKTEST_ENGINE_VERSION
                        or report.get("strategy_signature") != signature or report.get("status") != "success"
                        or report.get("calendar_verified") is not True):
                    continue
                weights = report.get("weights")
                if not isinstance(weights, dict):
                    continue
                from src.strategy.strategy_backtest import strategy_weights
                statistics = report.get("strategies")
                if not isinstance(statistics, list) or any(not isinstance(item, dict) for item in statistics):
                    continue
                computed = strategy_weights(statistics)
                return {key: value for key, value in computed.items() if key in names
                        and finite_number(weights.get(key)) == value}
            return {}
        except Exception as e:
            logger.debug(f"读取策略权重失败: {e}")
            return {}

    # ---- 查询 ----

    def latest(self) -> list[dict[str, Any]]:
        """最近一次选股结果（按股票合并），附次日涨幅（已有次日行情时）。"""
        return self.picks()

    def picks(self, trade_date: str | None = None, strategy: str | None = None) -> list[dict[str, Any]]:
        """某天的选股结果（按股票合并），附次日涨幅；trade_date 为空取最近一天。

        strategy 非空时只保留入选了该策略的股票，合并后的 labels/reasons 仍含该股票当天所有策略。
        """
        labels = {s.name: s.label for s in self.strategies}
        with get_db_session(self.db_path) as session:
            if not trade_date:
                trade_date = session.query(func.max(StrategyPick.trade_date)).scalar()
            if not trade_date:
                return []
            rows = (
                session.query(StrategyPick).filter(StrategyPick.trade_date == trade_date)
                .order_by(StrategyPick.score.desc(), StrategyPick.id).all()
            )
            if strategy:
                keep = {r.code for r in rows if r.strategy == strategy}
                rows = [r for r in rows if r.code in keep]
            if not rows:
                return []
            next_changes = self._next_day_changes(session, trade_date, {r.code for r in rows})
            merged: dict[str, dict[str, Any]] = {}
            for r in rows:
                item = merged.setdefault(r.code, {
                    "trade_date": r.trade_date, "code": r.code, "name": r.name, "scores": [], "close": r.close,
                    "change_pct": r.change_pct, "fits_regime": r.fits_regime, "labels": [], "reasons": [],
                    "next_change_pct": next_changes.get(r.code),
                })
                item["scores"].append(r.score or 0.0)
                item["fits_regime"] = item["fits_regime"] or r.fits_regime
                item["labels"].append(labels.get(r.strategy, r.strategy))
                item["reasons"].append(r.reason)
                if r.metadata_json:
                    try:
                        metadata = json.loads(r.metadata_json)
                        if isinstance(metadata, dict):
                            item.update(metadata)
                    except (TypeError, ValueError):
                        pass
        for item in merged.values():
            if "screen_score" not in item:
                item["score"] = _merged_score(item["scores"])
            item.pop("scores", None)
        return sorted(merged.values(), key=lambda x: (not x["fits_regime"], -x["score"], x["code"]))

    def history_dates(self, limit: int = 60) -> list[dict[str, Any]]:
        """最近 limit 个有选股的交易日（倒序）：入选数、各策略入选数、次日表现概览。"""
        limit = max(1, min(int(limit), 250))
        with get_db_session(self.db_path) as session:
            dates = [d for (d,) in (
                session.query(StrategyPick.trade_date).distinct()
                .order_by(StrategyPick.trade_date.desc()).limit(limit).all()
            )]
            if not dates:
                return []
            rows = session.query(StrategyPick.trade_date, StrategyPick.code, StrategyPick.strategy).filter(
                StrategyPick.trade_date.in_(dates)).all()
            by_date: dict[str, list] = defaultdict(list)
            for trade_date, code, strategy in rows:
                by_date[trade_date].append((code, strategy))
            result = []
            for trade_date in dates:
                items = by_date.get(trade_date, [])
                codes = {c for c, _ in items}
                strategies: dict[str, int] = defaultdict(int)
                for _, st in {(c, st) for c, st in items}:
                    strategies[st] += 1
                changes = list(self._next_day_changes(session, trade_date, codes).values())
                result.append({
                    "trade_date": trade_date, "picks": len(codes), "strategies": dict(strategies),
                    "evaluated": len(changes),
                    "avg_next_pct": round(sum(changes) / len(changes), 2) if changes else None,
                    "win_rate": round(sum(1 for c in changes if c > 0) / len(changes) * 100, 1) if changes else None,
                })
        return result

    def performance(self, lookback_days: int = 30) -> list[dict[str, Any]]:
        """近 lookback_days 天各策略选股的次日表现：平均涨幅、上涨比例、涨停比例。"""
        trading_calendar.load(self.db_path, refresh=False)
        start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        stats: dict[str, dict[str, Any]] = {s.name: {"picks": 0, "changes": [], "limit_ups": 0} for s in self.strategies}
        with get_db_session(self.db_path) as session:
            rows = session.query(StrategyPick).filter(StrategyPick.trade_date >= start).all()
            by_date: dict[str, list] = defaultdict(list)
            for r in rows:
                by_date[r.trade_date].append(r)
            for trade_date, picks in by_date.items():
                next_changes = self._next_day_changes(session, trade_date, {p.code for p in picks})
                for p in picks:
                    s = stats.setdefault(p.strategy, {"picks": 0, "changes": [], "limit_ups": 0})
                    s["picks"] += 1
                    change = next_changes.get(p.code)
                    if change is None:
                        continue
                    s["changes"].append(change)
                    if change >= daily_limit_pct(p.code, p.name or "") * 100 - LIMIT_TOLERANCE:
                        s["limit_ups"] += 1
        labels = {s.name: (s.label, s.regimes) for s in (*STRATEGIES, *self.strategies)}
        result = []
        for name, s in stats.items():
            changes = s["changes"]
            label, regimes = labels.get(name, (name, ()))
            result.append({
                "strategy": name, "label": label, "regimes": "/".join(regimes), "picks": s["picks"], "evaluated": len(changes),
                "avg_next_pct": round(sum(changes) / len(changes), 2) if changes else None,
                "win_rate": round(sum(1 for c in changes if c > 0) / len(changes) * 100, 1) if changes else None,
                "limit_up_rate": round(s["limit_ups"] / len(changes) * 100, 1) if changes else None,
            })
        return result

    @staticmethod
    def _next_day_changes(session, trade_date: str, codes: set[str]) -> dict[str, float]:
        next_day = trading_calendar.next_trade_day(trade_date).strftime("%Y-%m-%d")
        if next_day > datetime.now().strftime("%Y-%m-%d"):
            return {}
        variants = [v for c in codes for v in code_candidates(c)]
        result, selected = {}, {}
        for i in range(0, len(variants), CHUNK):
            for row in (
                session.query(StockDaily.code, StockDaily.change_pct, StockDaily.updated_at, StockDaily.id)
                .filter(StockDaily.trade_date == next_day, StockDaily.code.in_(variants[i:i + CHUNK]))
                .all()
            ):
                code, change = equity_code(row.code), finite_number(row.change_pct)
                if code and change is not None and (code not in selected or row_priority(row) > row_priority(selected[code])):
                    selected[code], result[code] = row, change
        return result
