"""决策信号 API。"""

from __future__ import annotations

from datetime import datetime

import pytest

from src import trading_calendar
from src.database.db import get_db_session
from src.database.models import DecisionSignal, StockDaily
from tests.test_api import env, _wait  # noqa: F401


@pytest.fixture(autouse=True)
def cal():
    trading_calendar._set_days({"2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"})
    yield
    trading_calendar._set_days(set())


def _add(config, **kw):
    base = dict(code="600519", name="贵州茅台", action="buy", score=80, confidence="高", trade_date="2026-09-21",
                horizon_days=5, status="active", expires_on="2026-09-28")
    base.update(kw)
    with get_db_session(config["database"]["sqlite_path"]) as s:
        row = DecisionSignal(**base)
        s.add(row)
        s.flush()
        return row.id


@pytest.fixture
def seeded(env):
    client, app, config = env
    ids = [_add(config, code="600519"), _add(config, code="601919", name="中远海控", action="sell", status="expired"),
           _add(config, code="600519", status="expired", ret_3d=2.0, ret_1d=1.0, ret_5d=3.0, evaluated_at=datetime(2026, 9, 25))]
    return client, config, ids


def test_list_filters_paging(seeded):
    client, _, ids = seeded
    r = client.get("/api/v1/signals").json()
    assert r["total"] == 3
    assert client.get("/api/v1/signals", params={"code": "601919"}).json()["total"] == 1
    assert client.get("/api/v1/signals", params={"status": "expired"}).json()["total"] == 2
    assert client.get("/api/v1/signals", params={"action": "sell"}).json()["total"] == 1
    p = client.get("/api/v1/signals", params={"limit": 1, "offset": 1}).json()
    assert p["total"] == 3 and len(p["items"]) == 1


@pytest.mark.parametrize("limit", [0, 201, -1])
def test_limit_range(env, limit):
    client, _, _ = env
    assert client.get("/api/v1/signals", params={"limit": limit}).status_code == 422


def test_stats_and_review(seeded):
    client, _, _ = seeded
    assert client.get("/api/v1/signals/stats").status_code == 200
    r = client.get("/api/v1/signals/review/600519")
    assert r.status_code == 200
    body = r.json()
    for k in ("samples", "hits", "hit_rate", "text"):
        assert k in body


def test_get_detail_and_404(seeded):
    client, _, ids = seeded
    assert client.get(f"/api/v1/signals/{ids[0]}").json()["code"] == "600519"
    assert client.get("/api/v1/signals/99999").status_code == 404


def test_feedback(seeded):
    client, _, ids = seeded
    r = client.put(f"/api/v1/signals/{ids[0]}/feedback", json={"feedback": "useful", "note": "好"})
    assert r.status_code == 200
    got = client.get(f"/api/v1/signals/{ids[0]}").json()
    assert got["feedback"] == "useful" and got["feedback_note"] == "好"
    assert client.put(f"/api/v1/signals/{ids[0]}/feedback", json={"feedback": "bogus"}).status_code == 422
    assert client.put("/api/v1/signals/99999/feedback", json={"feedback": "useful"}).status_code == 404


def test_evaluate_task(seeded):
    client, config, ids = seeded
    with get_db_session(config["database"]["sqlite_path"]) as s:
        s.add(StockDaily(code="600519", name="x", trade_date="2026-09-21", close=10, high=10, low=10))
        s.add(StockDaily(code="600519", name="x", trade_date="2026-09-22", close=10.5, high=10.5, low=10.5))
    task = client.post("/api/v1/signals/evaluate").json()
    done = _wait(client, task)
    assert done["status"] == "done"
    assert "evaluated" in done["result"]
