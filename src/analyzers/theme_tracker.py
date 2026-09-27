"""
主线（板块）追踪（参考 daily_stock_analysis 的板块热度趋势与题材阶段）

用涨停池历史按「所属行业」计算每个板块每天的热度：
    热度 = min(100, 10 × 涨停家数 + 8 × (最高连板 - 1) + 5 × 连板家数)
再看最近 5 个交易日：
- 趋势 = 最新热度 - 窗口首日热度；降温 = max(前一日热度 - 最新热度, 0)
- 持续性 = 热度达到 40 的天数占比（至少 3 个交易日的数据才判为持续发酵）
阶段：启动 / 加速 / 持续发酵 / 降温 / 退潮 / 观察。
龙头 = 最新交易日该板块连板最高（同高度取首次封板更早）的股票，其余涨停股为跟风。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from loguru import logger

from src import trading_calendar
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import LimitUpStock
from src.utils.stock_code import bare_code

WINDOW_DAYS = 5
HOT_HEAT = 40.0
ACCELERATE_TREND = 20.0
COOLING_DROP = 20.0
PERSISTENT_RATIO = 60.0
MIN_PERSISTENT_DAYS = 3  # 至少 3 个交易日的数据才谈得上「持续」
UNKNOWN_SECTORS = {"", "未知", "None", "nan"}


def day_heat(limit_up: int, max_height: int, multi_board: int) -> float:
    return float(min(100, 10 * limit_up + 8 * max(max_height - 1, 0) + 5 * multi_board))


@dataclass
class ThemeStatus:
    name: str
    phase: str
    heat: float                        # 最新交易日热度
    trend: float
    cooling: float
    persistence: float                 # 窗口内热度达标天数占比 %
    limit_up: int = 0                  # 最新交易日涨停家数
    max_height: int = 0
    ladder: str = ""                   # 梯队，如 "5板1 3板1 首板4"
    leader: dict[str, Any] = field(default_factory=dict)       # {"code","name","height"}
    followers: list[dict[str, Any]] = field(default_factory=list)
    heat_history: list[float] = field(default_factory=list)

    def brief(self) -> str:
        leader = f"龙头{self.leader['name']}({self.leader['height']}板)" if self.leader else "无龙头"
        return f"{self.name}【{self.phase}】热度{self.heat:.0f} 涨停{self.limit_up}家 梯队[{self.ladder}] {leader}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ThemeTracker:
    """基于涨停池历史的主线追踪。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")

    def analyze(self, trade_date: str | None = None, window: int = WINDOW_DAYS) -> list[ThemeStatus]:
        """返回最新交易日有涨停、或窗口内曾经热门的板块，按最新热度降序。"""
        try:
            with get_db_session(self.db_path) as session:
                query = session.query(LimitUpStock.trade_date).distinct()
                if trade_date:
                    query = query.filter(LimitUpStock.trade_date <= trade_date)
                recent = [d for (d,) in query.order_by(LimitUpStock.trade_date.desc()).limit(window * 3).all()]
                dates = sorted(trading_calendar.trade_days_only(recent)[:window])
                if not dates:
                    return []
                rows = (
                    session.query(LimitUpStock.trade_date, LimitUpStock.code, LimitUpStock.name, LimitUpStock.sector,
                                  LimitUpStock.continuous_days, LimitUpStock.first_limit_time)
                    .filter(LimitUpStock.trade_date.in_(dates))
                    .all()
                )
        except Exception as e:
            logger.warning(f"主线追踪失败: {e}")
            return []

        by_sector_day: dict[str, dict[str, list[tuple]]] = defaultdict(lambda: defaultdict(list))
        for d, code, name, sector, days, first_time in rows:
            sector = (sector or "").strip()
            if sector in UNKNOWN_SECTORS:
                continue
            by_sector_day[sector][d].append((bare_code(code), name or "", int(days or 1), first_time or "99:99"))

        latest = dates[-1]
        themes = []
        for sector, days_map in by_sector_day.items():
            history = []
            for d in dates:
                stocks = days_map.get(d, [])
                heights = [s[2] for s in stocks]
                history.append(day_heat(len(stocks), max(heights, default=0), sum(1 for h in heights if h >= 2)))
            today_stocks = days_map.get(latest, [])
            if not today_stocks and max(history) < HOT_HEAT:
                continue
            themes.append(self._status(sector, history, today_stocks))
        themes.sort(key=lambda t: (-t.heat, -t.max_height, t.name))
        return themes

    @staticmethod
    def _status(sector: str, history: list[float], today_stocks: list[tuple]) -> ThemeStatus:
        latest = history[-1]
        previous = history[-2] if len(history) >= 2 else latest
        trend = latest - history[0]
        cooling = max(previous - latest, 0.0)
        persistence = sum(1 for h in history if h >= HOT_HEAT) / len(history) * 100

        if latest == 0:
            phase = "退潮"
        elif cooling >= COOLING_DROP:
            phase = "降温"
        elif latest >= HOT_HEAT and trend >= ACCELERATE_TREND and persistence < PERSISTENT_RATIO:
            phase = "加速" if previous >= HOT_HEAT else "启动"
        elif latest >= HOT_HEAT and persistence >= PERSISTENT_RATIO and len(history) >= MIN_PERSISTENT_DAYS:
            phase = "持续发酵"
        elif latest >= HOT_HEAT:
            phase = "启动"
        else:
            phase = "观察"

        ordered = sorted(today_stocks, key=lambda s: (-s[2], s[3]))
        heights = defaultdict(int)
        for s in ordered:
            heights[s[2]] += 1
        ladder = " ".join(f"{'首板' if h == 1 else f'{h}板'}{n}" for h, n in sorted(heights.items(), reverse=True))
        leader = {"code": ordered[0][0], "name": ordered[0][1], "height": ordered[0][2]} if ordered else {}
        followers = [{"code": s[0], "name": s[1], "height": s[2]} for s in ordered[1:]]
        return ThemeStatus(
            name=sector, phase=phase, heat=latest, trend=round(trend, 1), cooling=round(cooling, 1),
            persistence=round(persistence, 1), limit_up=len(today_stocks), max_height=max(heights, default=0),
            ladder=ladder, leader=leader, followers=followers, heat_history=history,
        )

    @staticmethod
    def stock_roles(themes: list[ThemeStatus]) -> dict[str, dict[str, str]]:
        """{代码: {"theme": 板块, "phase": 阶段, "role": 龙头/跟风}}，只包含最新交易日的涨停股。"""
        roles: dict[str, dict[str, str]] = {}
        for t in themes:
            if t.leader:
                roles[t.leader["code"]] = {"theme": t.name, "phase": t.phase, "role": "龙头"}
            for f in t.followers:
                roles.setdefault(f["code"], {"theme": t.name, "phase": t.phase, "role": "跟风"})
        return roles

    @staticmethod
    def main_lines(themes: list[ThemeStatus], top: int = 5) -> list[ThemeStatus]:
        """主线：热度达标且不在降温/退潮阶段的板块。"""
        return [t for t in themes if t.heat >= HOT_HEAT and t.phase not in ("降温", "退潮")][:top]
