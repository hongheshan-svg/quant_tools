"""WS20 多策略会诊：策略挑选、会诊与共识、观点后验与权重、诊断接入、定时任务、API（离线）。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from src import trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import SkillOpinion, StockDaily
from src.services import skill_consult as sc
from src.services.skill_consult import SkillOpinionService, consensus, consult, section_text, select_skills
from src.services.strategy_skills import get_skill, reset_cache
from tests.test_api import env  # noqa: F401
from tests.test_stock_diagnosis import GOOD_REPLY, db_path as diag_db, offline_fundamentals  # noqa: F401


@pytest.fixture(autouse=True)
def _clean_skills():
    reset_cache()
    yield
    reset_cache()


def _names(skills):
    return [s.name for s in skills]


# ---------- select_skills ----------

def test_select_requested_resolves_names_dedupes_and_limits():
    picked = select_skills({}, "", max_skills=2, requested=["龙头战法", "dragon_head", "无效策略", "放量突破", "缩量回踩"])
    assert _names(picked) == ["dragon_head", "volume_breakout"]  # 中文名与英文名同一策略只留一个，无效忽略，最多 2 个
    assert select_skills({}, "", requested=["不存在", ""]) == []
    assert _names(select_skills({}, "", max_skills=3, requested=["缠论", "波浪理论", "综合"]))[:2] == ["chan_theory", "wave_theory"]


def test_select_limit_up_prefers_relay_and_dragon_head():
    sections = {"近期涨停": "【近期涨停】2026-09-25 2板 首封09:35 炸板0次 封单3.00亿 原因:固态电池"}
    assert _names(select_skills(sections, "")) == ["limit_up_relay", "dragon_head"]
    assert _names(select_skills(sections, "", max_skills=3)) == ["limit_up_relay", "dragon_head", "dragon_pullback"]
    # 近期无涨停不加分
    none = {"近期涨停": "【近期涨停】近期无涨停"}
    assert "limit_up_relay" not in _names(select_skills(none, "", max_skills=5))


def test_select_technical_and_volume_rules():
    sections = {"技术面": "【技术面】评分 80，趋势多头排列，MACD 金叉，量比 2.5"}
    assert set(_names(select_skills(sections, "", max_skills=3))) == {"bull_trend", "volume_breakout", "shrink_pullback"}
    low = {"技术面": "【技术面】趋势多头排列，量比 1.2"}
    assert "volume_breakout" not in _names(select_skills(low, "", max_skills=5))


def test_select_oversold_and_event_rules():
    down = {"近期走势": "【近期走势】09-01 收10.0；09-10 收8.5；09-20 收7.5"}
    assert _names(select_skills(down, "", max_skills=2)) == ["oversold_rebound", "bottom_volume"]
    up = {"近期走势": "【近期走势】09-01 收10.0；09-20 收9.5"}
    assert select_skills(up, "") == []
    assert _names(select_skills({"相关资讯": "【相关资讯】公司公告拟回购股份"}, "")) == ["event_driven"]
    assert _names(select_skills({"近 30 天公告": "【近 30 天公告】中标重大合同"}, "")) == ["event_driven"]


def test_select_regime_bonus_and_fallback_and_deterministic():
    picked = select_skills({}, "防守", max_skills=5)  # 全为 0 时取适配当前环境的
    assert picked and all("防守" in s.market_regimes for s in picked)
    assert all(s.name != "general" and s.display_name != "综合" for s in picked)
    sections = {"近期涨停": "【近期涨停】2板 原因:固态电池", "技术面": "【技术面】多头 量比 3.0"}
    first = _names(select_skills(sections, "均衡", max_skills=3))
    assert first == _names(select_skills(sections, "均衡", max_skills=3))
    assert len(first) == 3 and "general" not in first
    assert select_skills({}, "") == []


# ---------- consult / consensus ----------

class _Llm:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def chat_json(self, user_message, system_message="", **kw):
        self.calls.append((user_message, system_message))
        return self.handler(system_message)


def test_consult_one_call_per_skill_and_normalizes():
    skills = [get_skill("limit_up_relay"), get_skill("dragon_head"), get_skill("bull_trend")]
    replies = {
        skills[0].display_name: {"stance": "看多", "score": 95, "confidence": "高", "reason": "封板早"},
        skills[1].display_name: {"stance": "乱写", "score": -20, "confidence": "中", "reason": "x"},
    }

    def handler(system):
        for name, reply in replies.items():
            if name in system:
                return reply
        return ["not a dict"]  # 第三个策略返回非 dict

    llm = _Llm(handler)
    result = consult(llm, "股票：测试", skills)
    assert len(llm.calls) == 3 and all(c[0] == "股票：测试" for c in llm.calls)
    by = {o["skill"]: o for o in result}
    assert set(by) == {"limit_up_relay"}  # 非 dict、非法方向和越界评分均被隔离
    assert by["limit_up_relay"]["score"] == 95 and by["limit_up_relay"]["stance"] == "看多"
    for o in result:
        assert {"skill", "display_name", "stance", "score", "confidence", "reason", "weight"} <= set(o)
        assert o["weight"] == 1.0
    assert by["limit_up_relay"]["display_name"] == "打板接力"


def test_consult_weights_and_exceptions():
    skills = [get_skill("limit_up_relay"), get_skill("dragon_head")]

    def handler(system):
        if "打板接力" in system:
            raise RuntimeError("boom")
        return {"stance": "看空", "score": 30, "confidence": "低", "reason": "r"}

    result = consult(_Llm(handler), "ctx", skills, weights={"dragon_head": 1.2})
    assert [o["skill"] for o in result] == ["dragon_head"] and result[0]["weight"] == pytest.approx(1.2)
    assert consult(_Llm(handler), "ctx", []) == []


def test_consensus_thresholds_and_disagreement():
    def op(stance, score, weight=1.0):
        return {"stance": stance, "score": score, "weight": weight}

    assert consensus([]) == {}
    bull = consensus([op("看多", 80), op("看多", 60)])
    assert bull["stance"] == "看多" and bull["score"] == pytest.approx(70) and bull["agreement"] != "分歧"
    assert consensus([op("看空", 30), op("中性", 40)])["stance"] == "看空"
    assert consensus([op("中性", 50), op("中性", 55)])["stance"] == "中性"
    assert consensus([op("看多", 60)])["status"] == "insufficient"
    assert consensus([op("看空", 40)])["score"] is None
    split = consensus([op("看多", 80), op("看空", 20)])
    assert split["agreement"] == "分歧" and split["stance"] == "中性"
    weighted = consensus([op("看多", 90, 1.0), op("中性", 30, 3.0)])
    assert weighted["score"] == pytest.approx(57.3)  # 权重限制在 0.8–1.2


def test_section_text_contains_names():
    ops = [{"skill": "limit_up_relay", "display_name": "打板接力", "stance": "看多", "score": 80, "confidence": "高", "reason": "a", "weight": 1.0},
           {"skill": "dragon_head", "display_name": "龙头战法", "stance": "看空", "score": 20, "confidence": "低", "reason": "b", "weight": 0.9}]
    text = section_text(ops, consensus(ops))
    assert text.startswith("【策略会诊】") and "打板接力" in text and "龙头战法" in text


# ---------- SkillOpinionService ----------

CAL = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-09-01", "2026-10-30")
       if not ("2026-10-01" <= d.strftime("%Y-%m-%d") <= "2026-10-07")]


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "skill.db")
    _reset_db_engine()
    init_db(path)
    trading_calendar._set_days(set(CAL))
    yield path
    trading_calendar._set_days(set())
    _reset_db_engine()


@pytest.fixture
def svc(db):
    return SkillOpinionService({"database": {"sqlite_path": db}, "risk": {}, "trading": {}})


def _ops(*specs):
    return [{"skill": s, "display_name": s, "stance": st, "score": 70, "confidence": "高", "reason": "r", "weight": 1.0} for s, st in specs]


def _bars(db, code, rows):
    with get_db_session(db) as s:
        for d, c in rows:
            s.add(StockDaily(code=code, name="测试股", trade_date=d, open=c, close=c, high=c, low=c))


def _all(db):
    with get_db_session(db) as s:
        rows = s.query(SkillOpinion).order_by(SkillOpinion.id).all()
        for r in rows:
            s.expunge(r)
        return rows


def test_record_saves_rows(svc, db):
    assert svc.record(7, "600519", "测试股", "2026-09-21", _ops(("limit_up_relay", "看多"), ("dragon_head", "看空"))) == 2
    rows = _all(db)
    assert [(r.skill, r.stance, r.code, r.trade_date, r.diagnosis_id) for r in rows] == [
        ("limit_up_relay", "看多", "600519", "2026-09-21", 7), ("dragon_head", "看空", "600519", "2026-09-21", 7)]
    assert all(r.ret_5d is None and r.hit is None for r in rows)


def test_evaluate_needs_five_trading_days(svc, db):
    svc.record(1, "600519", "测试股", "2026-09-21", _ops(("a", "看多"), ("b", "看空"), ("c", "中性")))
    days = [d for d in CAL if d >= "2026-09-21"]
    _bars(db, "600519", [(days[0], 10.0), (days[1], 10.1), (days[2], 10.2), (days[3], 10.3)])
    svc.evaluate()
    assert all(r.ret_5d is None and r.hit is None for r in _all(db))  # 只有 3 个交易日后的日线

    _bars(db, "600519", [(days[4], 10.4), (days[5], 11.0)])  # 第 5 个交易日收 11.0，第 6 个日线不影响
    svc.evaluate()
    rows = {r.skill: r for r in _all(db)}
    assert rows["a"].ret_5d == pytest.approx(10.0)  # 第 5 个交易日收 11.0
    assert rows["a"].hit is True and rows["b"].hit is False and rows["c"].hit is None
    assert rows["c"].ret_5d is not None


def test_evaluate_bearish_hit_and_ignores_non_trade_rows(svc, db):
    svc.record(1, "600519", "测试股", "2026-09-21", _ops(("a", "看多"), ("b", "看空")))
    _bars(db, "600519", [("2026-09-21", 10.0), ("2026-09-22", 9.9), ("2026-09-23", 9.8), ("2026-09-24", 9.7), ("2026-09-25", 9.6),
                         ("2026-09-26", 9.0)])  # 周六，非交易日：不能算作第 5 个交易日
    svc.evaluate()
    assert all(r.ret_5d is None for r in _all(db))
    _bars(db, "600519", [("2026-09-28", 9.5)])
    svc.evaluate()
    rows = {r.skill: r for r in _all(db)}
    assert rows["a"].ret_5d == pytest.approx(-5.0) and rows["a"].hit is False
    assert rows["b"].hit is True


def test_performance_only_counts_hit_samples_and_percent(svc, db):
    with get_db_session(db) as s:
        for i in range(10):
            s.add(SkillOpinion(code=f"600{i:03d}", skill="limit_up_relay", stance="看多", score=70, trade_date="2026-09-21",
                               ret_5d=1.0, hit=i < 6, created_at=datetime.now()))
        for _ in range(5):  # 中性无 hit，不计入
            s.add(SkillOpinion(code="600519", skill="limit_up_relay", stance="中性", score=50, trade_date="2026-09-21", created_at=datetime.now()))
        s.add(SkillOpinion(code="600519", skill="dragon_head", stance="看空", score=20, trade_date="2026-09-21",
                           ret_5d=-2.0, hit=True, created_at=datetime.now() - timedelta(days=200)))  # 超出统计窗口
    perf = {p["skill"]: p for p in svc.performance(90)}
    assert set(perf) == {"limit_up_relay"}
    row = perf["limit_up_relay"]
    assert row["samples"] == 10 and row["hits"] == 6 and row["hit_rate"] == pytest.approx(60.0)
    assert row["weight"] == 1.0  # 独立样本不足 30
    assert {"skill", "display_name", "samples", "hits", "hit_rate", "avg_ret", "weight"} <= set(row)
    assert "dragon_head" in {p["skill"] for p in svc.performance(365)}


def test_weights_rules(svc, db):
    def add(skill, n, hits):
        with get_db_session(db) as s:
            for i in range(n):
                s.add(SkillOpinion(code=f"600{i:03d}", skill=skill, stance="看多", score=70, trade_date="2026-09-21",
                                   ret_5d=1.0 if i < hits else -1.0, hit=i < hits, created_at=datetime.now()))

    add("high", 40, 32)
    add("low", 40, 16)
    add("small", 29, 29)  # 样本不足 -> 1.0
    add("capped_low", 30, 0)
    w = svc.weights(90)
    assert w["high"] == pytest.approx(1.109, abs=0.001) and w["low"] == pytest.approx(0.964, abs=0.001)
    assert w["small"] == 1.0 and w["capped_low"] == pytest.approx(0.824, abs=0.001)
    assert svc.weights(90).get("unknown", 1.0) == 1.0


# ---------- 诊断接入 ----------

class _DiagLlm:
    """按提示词区分：策略会诊 / 决策员 / 分析员。"""

    def __init__(self, fail_skill: str | None = None):
        self.fail_skill, self.calls = fail_skill, []

    def chat_json(self, user_message, system_message="", **kw):
        kind = "consult" if "策略标准" in system_message else "decision" if "决策仪表盘" in system_message else "analyst"
        self.calls.append((kind, user_message, system_message))
        if kind == "consult":
            if self.fail_skill and self.fail_skill in system_message:
                raise RuntimeError("boom")
            return {"stance": "看多", "score": 75, "confidence": "高", "reason": "策略观点XYZ"}
        if kind == "decision":
            return dict(GOOD_REPLY)
        return {"view": "看多", "score": 70, "confidence": "中", "key_points": ["k"]}

    def kinds(self):
        return [c[0] for c in self.calls]


def _diag(db_path, llm, enabled: bool):
    from src.services.stock_diagnosis import StockDiagnosisService

    cfg = {"database": {"sqlite_path": db_path}, "risk": {}, "trading": {}}
    if enabled is not None:
        cfg["diagnosis"] = {"skill_consult": {"enabled": enabled, "max_skills": 2}}
    return StockDiagnosisService(cfg, llm=llm)


def test_diagnose_with_consult_enabled(diag_db):
    llm = _DiagLlm()
    result = _diag(diag_db, llm, True).diagnose("002594", force=True)
    assert not result.get("error")
    assert len(result["skill_opinions"]) >= 1
    assert result["skill_consensus"]["stance"] == "看多"
    decision_msgs = [c[1] for c in llm.calls if c[0] == "decision"]
    assert len(decision_msgs) == 1 and "【策略会诊】" in decision_msgs[0]
    assert llm.kinds().count("consult") == len(result["skill_opinions"])
    with get_db_session(diag_db) as s:
        rows = s.query(SkillOpinion).all()
        assert len(rows) == len(result["skill_opinions"]) and {r.code for r in rows} == {"002594"}
        assert all(r.diagnosis_id for r in rows)


def test_diagnose_consult_disabled_no_extra_calls(diag_db):
    for enabled in (False, None):
        llm = _DiagLlm()
        result = _diag(diag_db, llm, enabled).diagnose("002594", force=True)
        assert not result.get("error")
        assert not result.get("skill_opinions")
        assert llm.kinds() == ["decision"]
        assert "【策略会诊】" not in llm.calls[0][1]
    with get_db_session(diag_db) as s:
        assert s.query(SkillOpinion).count() == 0


def test_diagnose_survives_failed_skill(diag_db):
    llm = _DiagLlm(fail_skill="打板接力")
    result = _diag(diag_db, llm, True).diagnose("002594", force=True)
    assert not result.get("error") and result["action"] == "buy"
    assert all(o["skill"] != "limit_up_relay" for o in result["skill_opinions"])

    class _AllFail(_DiagLlm):
        def chat_json(self, user_message, system_message="", **kw):
            if "策略标准" in system_message:
                raise RuntimeError("boom")
            return super().chat_json(user_message, system_message, **kw)

    result = _diag(diag_db, _AllFail(), True).diagnose("002594", force=True)
    assert not result.get("error") and not result.get("skill_opinions")


# ---------- 定时任务 ----------

def test_signal_lifecycle_calls_skill_evaluate(monkeypatch):
    from src import scheduler
    from src.services.decision_signals import DecisionSignalService

    calls = []
    monkeypatch.setattr(scheduler, "_skip_non_trade_day", lambda config, name: False)
    monkeypatch.setattr(DecisionSignalService, "evaluate", lambda self, *a, **k: {"evaluated": 0})
    monkeypatch.setattr(SkillOpinionService, "evaluate", lambda self, *a, **k: calls.append(1) or {"evaluated": 0})
    scheduler._run_signal_lifecycle({"database": {"sqlite_path": ":memory:"}})
    assert calls == [1]


# ---------- API ----------

def test_api_skill_performance(env):
    client, app, config = env
    assert client.get("/api/v1/chat/skills/performance?days=90").json() == []
    with get_db_session(config["database"]["sqlite_path"]) as s:
        for i in range(4):
            s.add(SkillOpinion(code=f"600{i:03d}", skill="limit_up_relay", stance="看多", score=70, trade_date="2026-09-21",
                               ret_5d=2.0, hit=i < 3, created_at=datetime.now()))
    r = client.get("/api/v1/chat/skills/performance?days=90")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    assert {"skill", "display_name", "samples", "hits", "hit_rate", "avg_ret", "weight"} <= set(row)
    assert row["display_name"] == "打板接力" and row["samples"] == 4 and row["hits"] == 3
    assert row["hit_rate"] == pytest.approx(75.0) and row["weight"] == 1.0
