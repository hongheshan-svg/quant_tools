"""上游对齐回归：身份隔离、无效观点、失败语义、脱敏及研究证据。"""

from __future__ import annotations

import json

import pytest

from api.tasks import TaskManager, business_result_error
from src.services.diagnosis_agents import _normalize as normalize_analyst
from src.services.run_log import RunLog
from src.services.skill_consult import _normalize, consensus
from src.services.strategy_skills import get_skill
from src.utils.redaction import MASK, redact, redact_text
from src.utils.stock_code import StockCodeError, bare_code, code_candidates, diagnosis_code, resolve_identity
from tests.test_api import env  # noqa: F401


@pytest.mark.parametrize("code", ["SH600519", "600519.SH", "sh.600519", "６００５１９", "600519"])
def test_identity_aliases_have_one_canonical_stock(code):
    assert resolve_identity(code).code == "600519"
    assert bare_code(code) == "600519"
    assert all("sz" not in alias and "bj" not in alias for alias in code_candidates(code))


@pytest.mark.parametrize("code", ["SZ600519", "600519.SZ", "BJ000001", "SH600519.SZ", "abc", "60051"])
def test_invalid_identity_cannot_be_silently_rewritten(code):
    with pytest.raises(StockCodeError):
        resolve_identity(code)


def test_index_identity_never_reads_same_digits_stock():
    assert diagnosis_code("000001.SH") == "sh000001"
    assert diagnosis_code("000001") == "000001"
    assert "000001" not in code_candidates("sh000001")


@pytest.mark.parametrize("field,value", [("stance", "UNRECOGNIZED"), ("stance", "看多或看空"), ("confidence", "unknown"),
                                          ("score", float("nan")), ("score", float("inf")), ("score", 101),
                                          ("score", -1), ("score", True), ("score", None)])
def test_invalid_opinions_do_not_vote(field, value):
    raw = {"stance": "看多", "score": 95, "confidence": "高", field: value}
    assert _normalize(get_skill("volume_breakout"), raw, 1) is None
    assert consensus([raw]) == {}
    analyst = {"view": raw["stance"], "score": raw["score"], "confidence": raw["confidence"]}
    assert normalize_analyst("technical", analyst).get("error")


def test_one_valid_opinion_cannot_claim_agreement():
    valid = {"stance": "看多", "score": 95, "confidence": "高"}
    result = consensus([valid, {"stance": "wrong", "score": 95}])
    assert result["status"] == "insufficient" and result["score"] is None


@pytest.mark.parametrize("text,secret", [("api_key=SYNTHETIC_DEMO_ONLY", "SYNTHETIC_DEMO_ONLY"),
                                         ('{"password": "demo password"}', "demo password"),
                                         ("Authorization: Bearer DEMO_TOKEN_ONLY", "DEMO_TOKEN_ONLY"),
                                         ("https://user:demo-pass@host/api", "demo-pass"),
                                         ("https://hooks.slack.com/services/DEMO/ONLY", "DEMO/ONLY"),
                                         ("/Users/demo/private/settings.yaml", "demo/private")])
def test_sensitive_text_redacted_before_truncation(text, secret):
    assert secret not in redact_text(text)
    log = RunLog()
    with pytest.raises(ValueError):
        with log.step("取数"):
            raise ValueError(text)
    assert secret not in json.dumps(log.to_dict())
    assert redact({"details": {"api_key": secret}})["details"]["api_key"] == MASK


def test_business_task_failure_contract_is_explicit():
    tasks = TaskManager(workers=1)
    try:
        failed = tasks.submit("diagnosis", lambda: {"error": "api_key=FAKE_ERROR_SECRET"}, result_error=business_result_error)
        generic = tasks.submit("partial_batch", lambda: {"error": "部分标的失败", "completed": 2})
        tasks._pool.shutdown(wait=True)
        status = tasks.get(failed["id"])
        assert status["status"] == "error" and status["finished_at"]
        assert "FAKE_ERROR_SECRET" not in json.dumps(status)
        assert tasks.get(generic["id"])["status"] == "done"
    finally:
        tasks.shutdown()


def test_login_failures_block_even_correct_password_until_window_expires(env, monkeypatch):
    client, app, _ = env
    app.state.auth.set_password("correct-password")
    clock = [1000.0]
    monkeypatch.setattr("api.auth.monotonic", lambda: clock[0])
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"password": "wrong"}).status_code == 400
    limited = client.post("/api/v1/auth/login", json={"password": "correct-password"})
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
    clock[0] += 901
    assert client.post("/api/v1/auth/login", json={"password": "correct-password"}).status_code == 200
    assert not app.state.auth._failures


def test_invalid_stock_api_fails_before_scheduling_or_data_fetch(env):
    client, app, _ = env
    for route in ("daily", "diagnosis", "profile"):
        assert client.get(f"/api/v1/stocks/600519.SZ/{route}").status_code == 400
    assert client.post("/api/v1/stocks/SZ600519/diagnosis").status_code == 400
    assert app.state.tasks.list() == []


def test_context_pack_labels_estimation_and_failed_fetch_without_hiding_data():
    from src.services.research_artifact import build_context_pack
    log = RunLog()
    log.add("业绩", False, detail="api_key=DEMO_ONLY")
    pack = build_context_pack({"code": "600519", "name": "贵州茅台", "quote": {"close": 10, "trade_date": "2026-09-30", "pe": None},
                               "chip": {"source": "本地估算", "date": "2026-09-29", "avg_cost": 9},
                               "data_quality": {"score": 60, "missing": ["业绩"]}}, log)
    assert pack["blocks"]["chips"]["status"] == "estimated"
    assert pack["blocks"]["earnings"]["status"] == "fetch_failed"
    assert pack["blocks"]["quote"]["items"]["pe"]["status"] == "missing"
    assert pack["pack_version"] == "1.0"


def test_profile_includes_history_research_artifact_and_scoped_intelligence(env):
    from datetime import datetime
    from src.database.db import get_db_session
    from src.database.models import FinanceNews, StockDiagnosis
    client, _, config = env
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(FinanceNews(source="fixture", title="贵州茅台发布公告", news_time=datetime.now()))
        session.add(StockDiagnosis(code="600519", name="贵州茅台", trade_date="2026-09-30",
                                   result_json=json.dumps({"score": 50, "one_sentence": "观望"})))
    response = client.get("/api/v1/stocks/600519.SH/profile", params={"history_days": 30})
    assert response.status_code == 200
    profile = response.json()
    assert profile["history"]["data"]["total"] == 1
    assert profile["research"]["data"]["structured_report"]["subject"]["stock_code"] == "600519"
    assert profile["intelligence"]["data"]["items"][0]["source"] == "fixture"
    assert client.get("/api/v1/stocks/600519/profile?history_days=0").status_code == 422
