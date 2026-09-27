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
    assert roles[first_leader] == {"theme": "半导体", "dimension": "行业", "phase": "加速", "role": "龙头"}
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


def test_concept_dimension_finds_cross_industry_theme(tmp_path):
    """「海峡两岸」分散在工程机械、商贸、医疗等不同行业，按行业看不出来，按题材能识别为主线。"""
    from src.analyzers.theme_tracker import is_generic_concept

    db_path = str(tmp_path / "concepts.db")
    _reset_db_engine()
    init_db(db_path)
    stocks = [
        ("600815", "厦工股份", "工程机械", 2, "0925", "海峡两岸+工程机械+盾构机"),
        ("002264", "新华都", "商贸零售", 1, "0930", "海峡两岸+AI营销"),
        ("603122", "合富中国", "医疗器械", 1, "0931", "海峡两岸+AI医疗"),
        ("002679", "福建金森", "林业", 1, "0932", "海峡两岸+福建国资"),
        ("000753", "漳州发展", "电力", 1, "0933", "海峡两岸+福建国资+绿色电力"),
        ("300001", "单只题材", "软件", 1, "1000", "矢量网络分析仪"),
        ("601811", "新华文轩", "出版", 5, "0925", "教科书发行+拟收购民族出版社"),  # 5 板个股自身的事件，热度高也不算题材
        ("300002", "业绩股A", "软件", 1, "1001", "半年报增长"),
        ("300003", "业绩股B", "软件", 1, "1002", "业绩增长+扭亏为盈"),
    ]
    with get_db_session(db_path) as session:
        for code, name, sector, days, first_time, concepts in stocks:
            session.add(LimitUpStock(code=code, name=name, trade_date="2026-09-24", sector=sector, continuous_days=days,
                                     first_limit_time=first_time, concepts=concepts))

    tracker = ThemeTracker({"database": {"sqlite_path": db_path}})
    concepts = {t.name: t for t in tracker.analyze(dimension="concept")}
    assert set(concepts) == {"海峡两岸", "福建国资"}  # 只属于一只股票的标签和业绩类原因不列出
    strait = concepts["海峡两岸"]
    assert (strait.dimension, strait.limit_up, strait.ladder, strait.leader["name"]) == ("题材", 5, "2板1 首板4", "厦工股份")
    assert strait.phase == "启动"
    assert [t.name for t in tracker.main_lines(tracker.analyze(dimension="concept"))] == ["海峡两岸"]  # 福建国资只有 2 家、热度未达标
    industry_heat = {t.name: t.heat for t in tracker.analyze(dimension="industry")}
    assert all(industry_heat[s] < 40 for s in ("工程机械", "商贸零售", "医疗器械", "林业", "电力"))  # 按行业看每个都不热

    roles = tracker.stock_roles(tracker.analyze_all())
    assert roles["000753"] == {"theme": "海峡两岸", "dimension": "题材", "phase": "启动", "role": "跟风"}  # 多个题材取最热的
    assert [is_generic_concept(t) for t in ("业绩增长", "半年报增长", "扭亏为盈", "海峡两岸", "人形机器人")] == [True, True, True, False, False]
    _reset_db_engine()
