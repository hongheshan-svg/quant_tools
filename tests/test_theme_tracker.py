from __future__ import annotations

from src.analyzers.theme_tracker import ThemeTracker, day_heat
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import LimitUpStock

DAYS = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _seed(db_path: str) -> None:
    """每个板块每天的涨停股 [(连板, 首次封板时间)]。"""
    plan = {
        "半导体": [[(1, "0930")], [(1, "0930")] * 2, [(1, "0930")] * 3, [(1, "0930")] * 4,
                   [(3, "0940"), (3, "0931"), (1, "1000"), (1, "1010"), (1, "1020"), (1, "1030")]],
        "银行": [[(1, "1000")] * 5] * 5,
        "煤炭": [[(1, "1000")] * 5] * 4 + [[(1, "1000")]],
        "传媒": [[(1, "1000")] * 5] * 3 + [[], []],
        "钢铁": [[], [], [], [], [(1, "1000")] * 4],
        "纺织": [[], [], [], [], [(1, "1000")]],
        "": [[(1, "1000")] * 9] * 5,  # 无板块信息的不参与
    }
    n = 0
    with get_db_session(db_path) as session:
        for sector, days in plan.items():
            for d, stocks in zip(DAYS, days):
                for height, first_time in stocks:
                    n += 1
                    session.add(LimitUpStock(code=f"{600000 + n}", name=f"{sector or 'X'}{n}", trade_date=d, sector=sector,
                                             continuous_days=height, first_limit_time=first_time))


def test_day_heat():
    assert day_heat(0, 0, 0) == 0
    assert day_heat(6, 3, 2) == 60 + 16 + 10
    assert day_heat(20, 8, 10) == 100


def test_theme_phases_leaders_and_ladder(tmp_path):
    db_path = str(tmp_path / "themes.db")
    _reset_db_engine()
    init_db(db_path)
    _seed(db_path)

    tracker = ThemeTracker({"database": {"sqlite_path": db_path}})
    themes = tracker.analyze()
    by_name = {t.name: t for t in themes}

    assert "" not in by_name
    assert {n: t.phase for n, t in by_name.items()} == {
        "半导体": "加速", "银行": "持续发酵", "煤炭": "降温", "传媒": "退潮", "钢铁": "启动", "纺织": "观察",
    }
    semi = by_name["半导体"]
    assert semi.heat_history == [10.0, 20.0, 30.0, 40.0, 86.0]
    assert (semi.limit_up, semi.max_height, semi.ladder) == (6, 3, "3板2 首板4")
    assert semi.leader["height"] == 3 and semi.leader["name"].startswith("半导体")
    first_leader = semi.leader["code"]
    assert [f["height"] for f in semi.followers] == [3, 1, 1, 1, 1]
    assert themes[0].name == "半导体"  # 按最新热度排序

    roles = tracker.stock_roles(themes)
    assert roles[first_leader] == {"theme": "半导体", "phase": "加速", "role": "龙头"}
    assert [t.name for t in tracker.main_lines(themes)] == ["半导体", "银行", "钢铁"]
    _reset_db_engine()


def test_predictor_tags_stock_roles(tmp_path):
    db_path = str(tmp_path / "themes.db")
    _reset_db_engine()
    init_db(db_path)
    _seed(db_path)
    from src.services.premarket_predictor import LimitUpPredictor

    predictor = LimitUpPredictor({"database": {"sqlite_path": db_path}, "llm": {"cache_enabled": False}})
    tracker = ThemeTracker({"database": {"sqlite_path": db_path}})
    leader = tracker.analyze()[0].leader
    info = {"stocks": [{"code": leader["code"], "name": leader["name"]}, {"code": "000001", "name": "不在涨停池"}]}

    context = predictor._attach_theme_roles(info)
    assert info["stocks"][0]["role"] == "龙头·半导体(加速)"
    assert "role" not in info["stocks"][1]
    assert "半导体【加速】" in context and "降温/退潮板块（回避跟风）：煤炭、传媒" in context
    _reset_db_engine()


def test_holiday_duplicate_pool_is_ignored(tmp_path):
    """桌面端过去会在节假日把上一交易日的涨停池按当天日期入库，主线热度不能把它算成新的一天。"""
    from src import trading_calendar

    db_path = str(tmp_path / "holiday.db")
    _reset_db_engine()
    init_db(db_path)
    with get_db_session(db_path) as session:
        for d in ("2026-09-24", "2026-09-25"):  # 09-25 中秋休市，是 09-24 的重复
            for i in range(4):
                session.add(LimitUpStock(code=f"{600000 + i}", name=f"S{i}", trade_date=d, sector="出版", continuous_days=1))
    trading_calendar._set_days({"2026-09-23", "2026-09-24", "2026-09-28"})
    try:
        theme = ThemeTracker({"database": {"sqlite_path": db_path}}).analyze()[0]
        assert theme.heat_history == [40.0]
        assert theme.phase == "启动"
    finally:
        trading_calendar._set_days(set())
        _reset_db_engine()
