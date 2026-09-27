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
    themes: dict[str, float] = field(default_factory=dict)  # 所属的当前主线 → 热度

    @property
    def is_limit_up(self) -> bool:
        return self.change_pct >= self.limit_pct - LIMIT_TOLERANCE


def compute_features(code: str, name: str, bars: list) -> Features | None:
    """bars：按日期升序的日线（最后一根为选股日），需有 close/high/low/volume/amount/change_pct/turnover 属性。"""
    today = bars[-1]
    if not today.close or today.close <= 0:
        return None
    limit_pct = daily_limit_pct(code, name) * 100
    f = Features(code=code, name=name, close=today.close, change_pct=today.change_pct or 0.0, amount=today.amount or 0.0,
                 turnover=today.turnover, limit_pct=limit_pct, bars=len(bars))
    closes = [b.close for b in bars if b.close]
    for n in (5, 10, 20, 60):
        if len(closes) >= n:
            setattr(f, f"ma{n}", sum(closes[-n:]) / n)

    prev = bars[:-1]
    # 各数据源的成交量单位不一致（股/手），成交额统一为元，用成交额比近似量比
    base = [b.amount for b in prev[-5:] if b.amount]
    if len(base) >= 3 and today.amount:
        f.vol_ratio = today.amount / (sum(base) / len(base))

    last20 = prev[-20:]
    if len(last20) >= 20 and all(b.close for b in last20):
        highs = [b.high or b.close for b in last20]
        lows = [b.low or b.close for b in last20]
        f.high_20 = max(highs)
        f.range_20 = (max(highs) / min(lows) - 1) * 100
        f.ret_20 = (today.close / last20[0].close - 1) * 100
    if len(bars) >= 10:
        peak = max(b.high or b.close or 0 for b in bars[-15:])
        f.pullback_15 = (today.close / peak - 1) * 100 if peak else None
    f.limit_ups_15 = sum(1 for b in prev[-15:] if (b.change_pct or 0) >= limit_pct - LIMIT_TOLERANCE)
    if today.high and today.low:
        f.close_pos = (today.close - today.low) / (today.high - today.low) if today.high > today.low else 1.0
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
        self.strategies = self._load_strategies(cfg.get("strategies") or {})
        self.pool_cfg = load_config(STOCK_POOL_CONFIG_PATH) or {}

    @staticmethod
    def _load_strategies(overrides: dict) -> list[Strategy]:
        result = []
        for s in STRATEGIES:
            custom = overrides.get(s.name) or {}
            if custom.get("enabled", True) is False:
                continue
            params = {**s.params, **{k: float(v) for k, v in custom.items() if k in s.params}}
            result.append(Strategy(s.name, s.label, s.description, s.regimes, params, s.rule))
        return result

    def run(self, trade_date: str | None = None, save: bool = True, point_in_time: bool = False) -> ScreenResult:
        """point_in_time=True 用于历史回测：只用 trade_date 当天及以前的数据，策略权重不生效。"""
        trading_calendar.load(self.db_path, refresh=False)
        with get_db_session(self.db_path) as session:
            trade_date = trade_date or self._latest_trade_date(session)
            if not trade_date:
                return ScreenResult(notes=["没有行情数据，请先采集"])
            result = ScreenResult(trade_date=trade_date)
            snapshot = self._snapshot(session, trade_date)
            pool = [r for r in snapshot.values() if self._in_pool(r)]
            candidates = [r for r in pool if (r.amount or 0) >= PRE_FILTER_AMOUNT]
            bars = self._history(session, trade_date, [r.code for r in candidates])
            stock_themes = self._stock_themes(session, trade_date)
        main_lines = self._main_lines(trade_date)
        result.regime = self._regime(trade_date, point_in_time)
        result.weights = {} if point_in_time else self.strategy_weights()

        features = []
        for row in candidates:
            code = bare_code(row.code)
            f = compute_features(code, row.name or "", bars.get(code) or [row])
            if f is None:
                continue
            f.themes = {t: main_lines[t] for t in stock_themes.get(code, ()) if t in main_lines}
            features.append(f)

        with_history = sum(1 for f in features if f.bars >= 21)
        result.stats = {"universe": len(snapshot), "pool": len(pool), "candidates": len(candidates), "with_history": with_history}
        if len(snapshot) < MIN_UNIVERSE:
            result.notes.append(f"{trade_date} 只有 {len(snapshot)} 只股票的行情，不是全市场，选股结果不完整")
        if features and with_history < len(features) * MIN_HISTORY_COVERAGE:
            start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=HISTORY_CALENDAR_DAYS)).strftime("%Y-%m-%d")
            result.notes.append(f"只有 {with_history}/{len(features)} 只股票有 20 日以上日线，依赖历史的策略会漏选；"
                                f"可运行 python scripts/fetch_history.py --mode daily --start-date {start} 补齐")
        if result.regime in ("", "未知"):
            result.notes.append("大盘环境未知，所有策略按适配处理")
        result.picks = self._rank(features, result, result.weights)
        if save:
            self._save(result)
        logger.info(f"策略选股 {trade_date}：全市场 {len(snapshot)} 只 → 股票池 {len(pool)} → 入选 {len(result.picks)} 只")
        return result

    def _rank(self, features: list[Features], result: ScreenResult, weights: dict[str, float] | None = None) -> list[Pick]:
        hits: dict[str, list[tuple[Strategy, float, str]]] = defaultdict(list)
        by_code = {f.code: f for f in features}
        for strategy in self.strategies:
            weight = (weights or {}).get(strategy.name, 1.0)
            matched = []
            for f in features:
                outcome = strategy.rule(f, strategy.params)
                if outcome:
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
            ))
        picks.sort(key=lambda p: (not p.fits_regime, -p.score, p.code))
        return picks[: self.max_total]

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
                          StockDaily.turnover, StockDaily.circ_mv)
            .filter(StockDaily.trade_date == trade_date, StockDaily.close > 0)
            .all()
        )
        return {bare_code(r.code): r for r in rows}

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
                              StockDaily.close, StockDaily.volume, StockDaily.amount, StockDaily.change_pct, StockDaily.turnover)
                .filter(StockDaily.code.in_(variants[i:i + CHUNK]), StockDaily.trade_date >= start,
                        StockDaily.trade_date <= trade_date, StockDaily.close > 0)
                .all()
            )
            for r in rows:
                by_code[bare_code(r.code)][r.trade_date] = r  # 同一天多种代码格式只留一条
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
        with get_db_session(self.db_path) as session:
            session.query(StrategyPick).filter(StrategyPick.trade_date == result.trade_date).delete()
            for p in result.picks:
                for strategy, score, reason in zip(p.strategies, p.scores, p.reasons):
                    session.add(StrategyPick(
                        trade_date=result.trade_date, strategy=strategy, code=p.code, name=p.name, score=score,
                        reason=reason, close=p.close, change_pct=p.change_pct, fits_regime=p.fits_regime,
                    ))

    def strategy_weights(self) -> dict[str, float]:
        """最近 30 天内一次历史回测得出的策略权重；没有回测或关闭 adaptive_strategy_weights 时为空（都按 1.0）。"""
        if not self.adaptive_weights:
            return {}
        try:
            from src.database.models import StrategyBacktest

            since = datetime.now() - timedelta(days=WEIGHTS_MAX_AGE_DAYS)
            with get_db_session(self.db_path) as session:
                row = (
                    session.query(StrategyBacktest.result_json).filter(StrategyBacktest.created_at >= since)
                    .order_by(StrategyBacktest.created_at.desc()).first()
                )
            if not row:
                return {}
            import json

            return {k: float(v) for k, v in (json.loads(row[0]).get("weights") or {}).items()}
        except Exception as e:
            logger.debug(f"读取策略权重失败: {e}")
            return {}

    # ---- 查询 ----

    def latest(self) -> list[dict[str, Any]]:
        """最近一次选股结果（按股票合并），附次日涨幅（已有次日行情时）。"""
        labels = {s.name: s.label for s in STRATEGIES}
        with get_db_session(self.db_path) as session:
            trade_date = session.query(func.max(StrategyPick.trade_date)).scalar()
            if not trade_date:
                return []
            rows = (
                session.query(StrategyPick).filter(StrategyPick.trade_date == trade_date)
                .order_by(StrategyPick.score.desc(), StrategyPick.id).all()
            )
            next_changes = self._next_day_changes(session, trade_date, {r.code for r in rows})
            merged: dict[str, dict[str, Any]] = {}
            for r in rows:
                item = merged.setdefault(r.code, {
                    "trade_date": r.trade_date, "code": r.code, "name": r.name, "scores": [], "close": r.close,
                    "change_pct": r.change_pct, "fits_regime": r.fits_regime, "labels": [], "reasons": [],
                    "next_change_pct": next_changes.get(r.code),
                })
                item["scores"].append(r.score or 0.0)
                item["labels"].append(labels.get(r.strategy, r.strategy))
                item["reasons"].append(r.reason)
        for item in merged.values():
            item["score"] = _merged_score(item.pop("scores"))
        return sorted(merged.values(), key=lambda x: (not x["fits_regime"], -x["score"], x["code"]))

    def performance(self, lookback_days: int = 30) -> list[dict[str, Any]]:
        """近 lookback_days 天各策略选股的次日表现：平均涨幅、上涨比例、涨停比例。"""
        trading_calendar.load(self.db_path, refresh=False)
        start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        stats: dict[str, dict[str, Any]] = {s.name: {"picks": 0, "changes": [], "limit_ups": 0} for s in STRATEGIES}
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
        labels = {s.name: (s.label, s.regimes) for s in STRATEGIES}
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
        result = {}
        for i in range(0, len(variants), CHUNK):
            for code, change in (
                session.query(StockDaily.code, StockDaily.change_pct)
                .filter(StockDaily.trade_date == next_day, StockDaily.code.in_(variants[i:i + CHUNK]))
                .all()
            ):
                if change is not None:
                    result[bare_code(code)] = change
        return result
