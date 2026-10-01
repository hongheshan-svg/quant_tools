"""WS34 决策风格：阈值边界、安全护栏、诊断集成、重新评估、决策信号、API。"""

from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

from src.database.db import get_db_session
from src.database.models import DecisionSignal, StockDiagnosis
from tests.test_api import env  # noqa: F401
from tests.test_stock_diagnosis import (  # noqa: F401
    GOOD_REPLY, _service, db_path, offline_fundamentals,
)

BUY_SCORE = {"conservative": 65, "balanced": 50, "aggressive": 45}
QUALITY = {"conservative": 75, "balanced": 60, "aggressive": 50}
OUTFLOW = {"conservative": -3.0, "balanced": -5.0, "aggressive": -8.0}
CJK = re.compile(r"[一-鿿]")


def inputs(**over) -> dict:
    """典型的「各风格都放行买入」快照；字段名以 decision_profile.decide 的 inputs 为准。"""
    base = {
        "score": 80.0, "action": "buy", "confidence": "高",
        "regime": {"regime": "均衡", "position_factor": 1.0},
        "data_quality": {"score": 90, "missing": [], "core_ok": True, "bar_count": 60},
        "flow_ratio": 0.0, "severe_notice": None, "disagreement": "", "calibration_bullish": None,
        "previous": None, "phase": None, "phase_decision": None, "quote_trade_date": "2026-09-25",
        "invalidation": "跌破 9 元", "stop_loss": 9.0,
    }
    base.update(over)
    return base


def decide(inp: dict, profile: str, lang: str = "zh"):
    from src.services.decision_profile import decide as _decide

    return _decide(json.loads(json.dumps(inp)), profile, lang)


def quality(score: int, missing=None) -> dict:
    return {"score": score, "missing": missing or ["筹码"], "core_ok": True, "bar_count": 60}


def text(guardrails) -> str:
    return "\n".join(guardrails)


# ---------- 基础常量 ----------

def test_constants_and_normalize():
    from src.services import decision_profile as dp
    from src.services import stock_diagnosis as sd

    assert dp.PROFILES == ("conservative", "balanced", "aggressive")
    assert dp.PROFILE_LABELS == {"conservative": "保守", "balanced": "均衡", "aggressive": "进取"}
    for bad in (None, "", "bogus", "BALANCED ", 3):
        assert dp.normalize_profile(bad) == "balanced"
    assert dp.normalize_profile("aggressive") == "aggressive"
    # balanced 的阈值必须与现有常量一致
    assert sd.MIN_BUY_SCORE == 50 and sd.MIN_DATA_QUALITY == 60 and sd.FLOW_OUTFLOW_RATIO == -5
    assert set(dp.PROFILE_RULES) == set(dp.PROFILES)


def test_good_case_passes_in_all_profiles():
    for profile in BUY_SCORE:
        action, confidence, notes = decide(inputs(), profile)
        assert (action, confidence) == ("buy", "高"), profile
    assert decide(inputs(), "balanced")[2] == []


# ---------- 阈值边界 ----------

@pytest.mark.parametrize("profile,low,ok", [("conservative", 64, 65), ("balanced", 49, 50), ("aggressive", 44, 45)])
def test_score_boundary(profile, low, ok):
    action, _, notes = decide(inputs(score=low), profile)
    assert action == "watch" and str(low) in text(notes)
    assert decide(inputs(score=ok), profile)[0] == "buy"
    # 加仓与买入同阈值
    assert decide(inputs(score=low, action="add"), profile)[0] == "watch"
    assert decide(inputs(score=ok, action="add"), profile)[0] == "add"


def test_score_downgrade_names_profile_for_non_balanced():
    assert "保守风格" in text(decide(inputs(score=58), "conservative")[2])
    assert "进取风格" in text(decide(inputs(score=40), "aggressive")[2])
    assert "评分 45 与「买入」不一致，降级为观望" in text(decide(inputs(score=45), "balanced")[2])


@pytest.mark.parametrize("profile,low,ok", [("conservative", 74, 75), ("balanced", 59, 60), ("aggressive", 49, 50)])
def test_data_quality_boundary(profile, low, ok):
    action, confidence, notes = decide(inputs(data_quality=quality(low)), profile)
    assert action == "watch" and f"数据完整度 {low}%" in text(notes)
    assert decide(inputs(data_quality=quality(ok)), profile)[0] == "buy"


def test_low_quality_sets_confidence_low_balanced():
    action, confidence, notes = decide(inputs(data_quality=quality(55)), "balanced")
    assert (action, confidence) == ("watch", "低")
    assert "数据完整度 55%（缺少筹码），不足以支撑买入，降级为观望" in text(notes)


def test_flow_outflow_boundary():
    # 保守：-2.9 放行，-3 降级
    assert decide(inputs(flow_ratio=-2.9), "conservative")[0] == "buy"
    assert decide(inputs(flow_ratio=-3.0), "conservative")[0] == "watch"
    # 均衡：-4.9 放行，-5 降级（旧逻辑 <=）
    assert decide(inputs(flow_ratio=-4.9), "balanced")[0] == "buy"
    assert decide(inputs(flow_ratio=-5.0), "balanced")[0] == "watch"
    # 进取：-7.9 放行，-8 降级
    assert decide(inputs(flow_ratio=-7.9), "aggressive")[0] == "buy"
    assert decide(inputs(flow_ratio=-8.0), "aggressive")[0] == "watch"
    assert "资金净流出占成交额 8.0%，与买入建议矛盾，降级为观望" in text(decide(inputs(flow_ratio=-8.0), "balanced")[2])
    # 没有资金流数据不降级
    assert decide(inputs(flow_ratio=None), "conservative")[0] == "buy"


def test_defensive_regime_by_profile():
    inp = inputs(regime={"regime": "防守", "position_factor": 0.5})
    action, _, notes = decide(inp, "conservative")
    assert action == "watch" and "保守风格" in text(notes)
    for profile in ("balanced", "aggressive"):
        action, _, notes = decide(inp, profile)
        assert action == "buy" and "大盘防守，新开仓仓位按 ×0.5 控制" in text(notes)


def test_low_confidence_buy_by_profile():
    inp = inputs(confidence="低")
    assert decide(inp, "conservative")[0] == "watch"
    assert decide(inp, "balanced")[0] == "buy"
    assert decide(inp, "aggressive")[0] == "buy"


def test_requires_invalidation_or_stop_loss():
    missing = inputs(invalidation="", stop_loss=None)
    assert decide(missing, "balanced")[0] == "buy"
    for profile in ("conservative", "aggressive"):
        action, _, notes = decide(missing, profile)
        assert action == "watch" and notes, profile
    # 有其一即可
    for profile in ("conservative", "aggressive"):
        assert decide(inputs(invalidation="", stop_loss=9.0), profile)[0] == "buy"
        assert decide(inputs(invalidation="跌破 9 元", stop_loss=None), profile)[0] == "buy"


def test_conservative_position_cap_note():
    notes = decide(inputs(), "conservative")[2]
    assert "保守风格：单只不超过 2 成" in text(notes)
    for profile in ("balanced", "aggressive"):
        assert "单只不超过 2 成" not in text(decide(inputs(), profile)[2])


def test_non_bullish_actions_untouched_by_profile():
    for action in ("hold", "watch", "reduce", "sell", "avoid"):
        for profile in BUY_SCORE:
            out, _, notes = decide(inputs(action=action, score=30, flow_ratio=-9, confidence="低"), profile)
            assert out == action, (action, profile)
            assert "单只不超过 2 成" not in text(notes)


# ---------- 安全护栏对所有风格生效 ----------

@pytest.mark.parametrize("profile", list(BUY_SCORE))
def test_safety_guardrails_apply_to_all_profiles(profile):
    freeze = decide(inputs(regime={"regime": "冰点", "position_factor": 0.0}), profile)
    assert freeze[0] == "watch" and "冰点" in text(freeze[2])

    core = decide(inputs(data_quality={"score": 90, "missing": [], "core_ok": False, "bar_count": 10}), profile)
    assert core[0] == "watch" and "日线 10 根" in text(core[2])

    notice = {"date": "2026-09-22", "title": "关于收到中国证监会立案告知书的公告", "risk": "立案", "severe": True}
    severe = decide(inputs(severe_notice=notice), profile)
    assert severe[0] == "watch" and "近 30 天公告含「立案」" in text(severe[2])

    previous = {"action": "sell", "score": 75, "created_at": "2026-09-24 15:00", "action_label": "卖出"}
    flipped = decide(inputs(score=80, previous=previous), profile)
    assert flipped[0] == "watch" and "方向相反" in text(flipped[2])
    # 分差足够大则不算反复
    assert decide(inputs(score=95, previous=previous), profile)[0] == "buy"


def test_disagreement_and_calibration_lower_confidence():
    for profile in ("balanced", "aggressive"):
        _, confidence, notes = decide(inputs(disagreement="看多 vs 看空"), profile)
        assert confidence == "中" and "分析员观点分歧" in text(notes)
        _, confidence, notes = decide(inputs(calibration_bullish={"n": 12, "accuracy": 40.0}), profile)
        assert confidence == "中" and "准确率仅 40.0%" in text(notes)
        # 样本不足不调整
        assert decide(inputs(calibration_bullish={"n": 5, "accuracy": 10.0}), profile)[1] == "高"


# ---------- 英文与纯函数性质 ----------

def test_english_guardrails_have_no_chinese():
    for profile in BUY_SCORE:
        notes = decide(inputs(score=40, flow_ratio=-9, data_quality=quality(40)), profile, lang="en")[2]
        assert notes and not CJK.search(text(notes)), (profile, notes)


def test_decide_is_pure_and_json_safe():
    inp = inputs(score=58)
    before = json.dumps(inp, sort_keys=True)
    a = decide(inp, "conservative")
    b = decide(inp, "conservative")
    assert a == b and json.dumps(inp, sort_keys=True) == before
    assert decide(inp, "bogus")[0:2] == decide(inp, "balanced")[0:2]  # 非法风格按均衡


# ---------- 诊断集成 ----------

def _diag_id(db_path_: str, code: str = "002594") -> int:
    with get_db_session(db_path_) as s:
        return s.query(StockDiagnosis).filter(StockDiagnosis.code == code).order_by(StockDiagnosis.id.desc()).first().id


WEAK_BUY = {**GOOD_REPLY, "score": 58}


def test_diagnose_writes_profile_and_inputs(db_path):
    service, _ = _service(db_path, GOOD_REPLY)
    result = service.diagnose("002594", force=True)
    assert result["decision_profile"] == "balanced"
    assert isinstance(result["decision_inputs"], dict) and result["decision_inputs"]
    json.dumps(result["decision_inputs"])  # 可序列化
    with get_db_session(db_path) as s:
        saved = json.loads(s.query(StockDiagnosis).one().result_json)
    assert saved["decision_profile"] == "balanced" and saved["decision_inputs"] == result["decision_inputs"]


def test_diagnose_profile_changes_decision(db_path):
    service, _ = _service(db_path, WEAK_BUY)
    balanced = service.diagnose("002594", force=True)
    conservative = service.diagnose("002594", force=True, profile="conservative")
    aggressive = service.diagnose("002594", force=True, profile="aggressive")
    assert balanced["action"] == "buy"
    assert conservative["action"] == "watch" and "保守风格" in text(conservative["guardrails"])
    assert aggressive["action"] == "buy"
    assert [r["decision_profile"] for r in (balanced, conservative, aggressive)] == ["balanced", "conservative", "aggressive"]


def test_diagnose_profile_from_config(db_path):
    from src.services.stock_diagnosis import StockDiagnosisService

    service = StockDiagnosisService({"database": {"sqlite_path": db_path}, "risk": {}, "trading": {},
                                     "diagnosis": {"decision_profile": "conservative"}},
                                    llm=_service(db_path, WEAK_BUY)[1])
    result = service.diagnose("002594", force=True)
    assert result["decision_profile"] == "conservative" and result["action"] == "watch"
    # 参数覆盖配置
    assert service.diagnose("002594", force=True, profile="aggressive")["decision_profile"] == "aggressive"


def test_cache_is_per_profile(db_path):
    service, llm = _service(db_path, GOOD_REPLY)
    service.diagnose("002594")
    assert service.diagnose("002594")["cached"] is True and len(llm.calls) == 1
    other = service.diagnose("002594", profile="conservative")
    assert not other.get("cached") and len(llm.calls) == 2
    again = service.diagnose("002594", profile="conservative")
    assert again["cached"] is True and again["decision_profile"] == "conservative" and len(llm.calls) == 2
    assert service.diagnose("002594")["decision_profile"] == "balanced"


def test_balanced_text_matches_legacy(db_path):
    weak = _service(db_path, {**GOOD_REPLY, "score": 45, "action": "买入"})[0].diagnose("002594", force=True)
    assert weak["action"] == "watch" and "评分 45 与「买入」不一致，降级为观望" in weak["guardrails"][0]


def test_fund_service_inherits(db_path):
    from src.services.fund_diagnosis import FundDiagnosisService

    assert issubclass(FundDiagnosisService, __import__("src.services.stock_diagnosis", fromlist=["x"]).StockDiagnosisService)


# ---------- 重新评估 ----------

def test_reassess_no_llm_and_changed(db_path, monkeypatch):
    from src.collectors import stock_news as stock_news_mod

    service, llm = _service(db_path, WEAK_BUY)
    service.diagnose("002594", force=True)
    did = _diag_id(db_path)
    calls = len(llm.calls)

    def boom(*a, **k):
        raise AssertionError("重新评估不应联网")

    monkeypatch.setattr(stock_news_mod, "get_stock_news", boom)
    cons = service.reassess(did, "conservative")
    assert len(llm.calls) == calls
    assert (cons["diagnosis_id"], cons["code"], cons["name"]) == (did, "002594", "比亚迪")
    assert (cons["profile"], cons["profile_label"]) == ("conservative", "保守")
    assert (cons["action"], cons["action_label"]) == ("watch", "观望")
    assert cons["changed"] is True and "保守风格" in text(cons["guardrails"])
    assert cons["original"]["profile"] == "balanced" and cons["original"]["action"] == "buy"
    assert cons["original"]["action_label"] == "买入"

    same = service.reassess(did, "balanced")
    assert same["changed"] is False and same["action"] == "buy"
    assert service.reassess(did, "aggressive")["changed"] is False
    assert len(llm.calls) == calls


def test_reassess_old_record_and_missing(db_path):
    service, _ = _service(db_path, GOOD_REPLY)
    with get_db_session(db_path) as s:
        row = StockDiagnosis(code="002594", name="比亚迪", trade_date="2026-09-25", action="buy", score=80,
                             result_json=json.dumps({"code": "002594", "name": "比亚迪", "action": "buy", "score": 80}))
        s.add(row)
        s.flush()
        old_id = row.id
    assert service.reassess(old_id, "conservative") == {"error": "该诊断是旧版本生成的，缺少重新评估所需的快照"}
    missing = service.reassess(99999, "conservative")
    assert "error" in missing


# ---------- 决策信号 ----------

@pytest.fixture
def sig(db_path):
    from src.services.decision_signals import DecisionSignalService

    return DecisionSignalService({"database": {"sqlite_path": db_path}, "risk": {}, "trading": {}})


def _result(profile=None, action="buy", code="600519"):
    r = {"code": code, "name": "测试股", "action": action, "score": 80, "confidence": "高", "trade_date": "2026-09-21",
         "horizon_days": 5, "invalidation": "跌破 9 元", "battle_plan": {"buy_price": 10.0, "stop_loss": 9.0, "target_price": 12.0}}
    if profile:
        r["decision_profile"] = profile
    return r


def _status(db_path_, sid):
    with get_db_session(db_path_) as s:
        return s.get(DecisionSignal, sid).status


def test_record_writes_profile(sig, db_path):
    assert sig.record_from_diagnosis(_result("aggressive"))["profile"] == "aggressive"
    default = sig.record_from_diagnosis(_result(None, code="601919"))
    assert default["profile"] == "balanced" and default["profile_label"] == "均衡"


def test_profiles_do_not_invalidate_each_other(sig, db_path):
    cons = sig.record_from_diagnosis(_result("conservative"))
    bal = sig.record_from_diagnosis(_result("balanced", action="sell"))  # 相反建议，但风格不同
    assert _status(db_path, cons["id"]) == "active" and _status(db_path, bal["id"]) == "active"
    # 同风格相反建议失效、同方向替代
    bal2 = sig.record_from_diagnosis(_result("balanced", action="buy"))
    assert _status(db_path, bal["id"]) == "invalidated" and _status(db_path, bal2["id"]) == "active"
    bal3 = sig.record_from_diagnosis(_result("balanced", action="buy"))
    assert _status(db_path, bal2["id"]) == "replaced" and _status(db_path, cons["id"]) == "active"
    assert bal3["status"] == "active"


def test_list_and_stats_filter_by_profile(sig, db_path):
    with get_db_session(db_path) as s:
        for code, profile in (("600001", "conservative"), ("600002", "balanced"), ("600003", "aggressive"),
                              ("600004", None), ("600005", None)):
            s.add(DecisionSignal(code=code, name=code, action="buy", score=80, trade_date="2026-09-21", horizon_days=5,
                                 status="active", profile=profile, created_at=datetime.now()))
    assert sig.list()["total"] == 5
    assert {i["code"] for i in sig.list(profile="conservative")["items"]} == {"600001"}
    assert sig.list(profile="aggressive")["total"] == 1
    unknown = sig.list(profile="unknown")
    assert unknown["total"] == 2 and all(i["profile"] in (None, "") for i in unknown["items"])
    assert sig.stats(90)["total"] == 5
    assert sig.stats(90, profile="balanced")["total"] == 1
    assert sig.stats(90, profile="unknown")["total"] == 2


def test_save_reassessed_created_existing_skipped(db_path, sig):
    service, _ = _service(db_path, WEAK_BUY)
    service.diagnose("002594", force=True, profile="conservative")  # 保守：观望
    did = _diag_id(db_path)
    skipped = sig.save_reassessed(did, "conservative")
    assert skipped["status"] == "skipped" and skipped["reason"]

    created = sig.save_reassessed(did, "aggressive")
    assert created["status"] == "created"
    signal = created["signal"]
    assert (signal["profile"], signal["action"], signal["diagnosis_id"]) == ("aggressive", "buy", did)
    assert signal["trade_date"] == "2026-09-25" and signal["stop_loss"] == 22.5

    existing = sig.save_reassessed(did, "aggressive")
    assert existing["status"] == "existing" and existing["signal"]["id"] == signal["id"]
    with get_db_session(db_path) as s:
        assert s.query(DecisionSignal).filter(DecisionSignal.diagnosis_id == did, DecisionSignal.profile == "aggressive").count() == 1


def test_save_reassessed_old_record(db_path, sig):
    with get_db_session(db_path) as s:
        row = StockDiagnosis(code="002594", name="比亚迪", trade_date="2026-09-25", action="buy", score=80,
                             result_json=json.dumps({"code": "002594", "action": "buy"}))
        s.add(row)
        s.flush()
        old_id = row.id
    out = sig.save_reassessed(old_id, "balanced")
    assert out.get("status") in ("skipped", "error") and "旧版本" in json.dumps(out, ensure_ascii=False)
    with get_db_session(db_path) as s:
        assert s.query(DecisionSignal).count() == 0


# ---------- API ----------

def _seed_diagnosis(config, score=58, with_inputs=True) -> int:
    result = {"code": "600519", "name": "贵州茅台", "action": "buy", "action_label": "买入", "score": score,
              "confidence": "高", "trade_date": "2026-09-25", "horizon_days": 5, "invalidation": "跌破 9 元",
              "battle_plan": {"buy_price": 10.0, "stop_loss": 9.0, "target_price": 12.0}, "decision_profile": "balanced",
              "guardrails": []}
    if with_inputs:
        result["decision_inputs"] = inputs(score=score)
    with get_db_session(config["database"]["sqlite_path"]) as s:
        row = StockDiagnosis(code="600519", name="贵州茅台", trade_date="2026-09-25", action="buy", score=score,
                             result_json=json.dumps(result, ensure_ascii=False))
        s.add(row)
        s.flush()
        return row.id


def test_api_reassess(env):
    client, _, config = env
    did = _seed_diagnosis(config)
    r = client.post(f"/api/v1/stocks/diagnoses/{did}/reassess", json={"profile": "conservative"})
    assert r.status_code == 200
    body = r.json()
    assert body["profile"] == "conservative" and body["action"] == "watch" and body["changed"] is True
    assert body["original"]["action"] == "buy" and "signal" not in body
    with get_db_session(config["database"]["sqlite_path"]) as s:
        assert s.query(DecisionSignal).count() == 0  # persist 默认 false 不落库

    r = client.post(f"/api/v1/stocks/diagnoses/{did}/reassess", json={"profile": "aggressive", "persist": True})
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "buy" and body["changed"] is False
    blob = json.dumps(body, ensure_ascii=False)
    assert "created" in blob
    again = client.post(f"/api/v1/stocks/diagnoses/{did}/reassess", json={"profile": "aggressive", "persist": True}).json()
    assert "existing" in json.dumps(again, ensure_ascii=False)
    with get_db_session(config["database"]["sqlite_path"]) as s:
        assert s.query(DecisionSignal).filter(DecisionSignal.profile == "aggressive").count() == 1

    skipped = client.post(f"/api/v1/stocks/diagnoses/{did}/reassess", json={"profile": "conservative", "persist": True})
    assert skipped.status_code == 200 and "skipped" in json.dumps(skipped.json(), ensure_ascii=False)


def test_api_reassess_errors(env):
    client, _, config = env
    did = _seed_diagnosis(config)
    assert client.post(f"/api/v1/stocks/diagnoses/{did}/reassess", json={"profile": "bogus"}).status_code == 422
    assert client.post(f"/api/v1/stocks/diagnoses/{did}/reassess", json={}).status_code == 422
    assert client.post("/api/v1/stocks/diagnoses/99999/reassess", json={"profile": "balanced"}).status_code == 404
    old = _seed_diagnosis(config, with_inputs=False)
    r = client.post(f"/api/v1/stocks/diagnoses/{old}/reassess", json={"profile": "balanced"})
    assert r.status_code == 400 and "旧版本" in json.dumps(r.json(), ensure_ascii=False)


def _add_signal(config, **kw):
    base = dict(code="600519", name="贵州茅台", action="buy", score=80, confidence="高", trade_date="2026-09-21",
                horizon_days=5, status="active", expires_on="2026-09-28", created_at=datetime.now())
    base.update(kw)
    with get_db_session(config["database"]["sqlite_path"]) as s:
        s.add(DecisionSignal(**base))


def test_api_signals_profile_filter(env):
    client, _, config = env
    _add_signal(config, code="600001", profile="conservative")
    _add_signal(config, code="600002", profile="aggressive")
    _add_signal(config, code="600003", profile=None)
    assert client.get("/api/v1/signals").json()["total"] == 3
    r = client.get("/api/v1/signals", params={"profile": "conservative"}).json()
    assert r["total"] == 1 and r["items"][0]["profile"] == "conservative" and r["items"][0]["profile_label"] == "保守"
    assert client.get("/api/v1/signals", params={"profile": "unknown"}).json()["total"] == 1
    assert client.get("/api/v1/signals/stats", params={"profile": "aggressive"}).json()["total"] == 1
    assert client.get("/api/v1/signals/stats", params={"profile": "unknown"}).json()["total"] == 1
    assert client.get("/api/v1/signals/stats").json()["total"] == 3


def _diag_settings(payload):
    return payload.get("diagnosis", payload)


def test_api_diagnosis_settings_roundtrip(env, monkeypatch):
    from src import config_loader, settings_store

    client, _, config = env
    monkeypatch.setattr(config_loader, "reload_config",
                        lambda: {**config, "diagnosis": settings_store.read_settings().get("diagnosis", {})})
    got = _diag_settings(client.get("/api/v1/settings/diagnosis").json())
    assert got["decision_profile"] == "balanced" or got["decision_profile"]
    new = {"decision_profile": "conservative", "mode": "full", "shareholders": True, "calibration": False,
           "signal_review": False, "skill_consult": {"enabled": True, "max_skills": 3}}
    r = client.put("/api/v1/settings/diagnosis", json={"diagnosis": new})
    assert r.status_code == 200, r.text
    saved = _diag_settings(r.json())
    for key, value in new.items():
        assert saved[key] == value, key
    again = _diag_settings(client.get("/api/v1/settings/diagnosis").json())
    assert again["decision_profile"] == "conservative" and again["skill_consult"]["max_skills"] == 3


@pytest.mark.parametrize("bad", [
    {"decision_profile": "bogus"}, {"mode": "weird"}, {"skill_consult": {"max_skills": 0}},
    {"skill_consult": {"max_skills": 6}}, {"shareholders": "maybe"},
])
def test_api_diagnosis_settings_validation(env, bad):
    client, _, _ = env
    r = client.put("/api/v1/settings/diagnosis", json={"diagnosis": bad})
    assert r.status_code == 422
