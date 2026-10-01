"""诊断历史：查询/详情/删除、Markdown 导出与分享图接口。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from urllib.parse import quote

import pytest

from src.database.db import get_db_session
from src.database.models import StockDiagnosis
from src.services import report_image
from tests.test_api import env, _wait  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\nfake"


def _add(config, code, name, action, score, created, raw=None, result=None):
    result = result if result is not None else {
        "action": action, "score": score, "one_sentence": f"{name}结论", "summary": f"{name}摘要",
        "code": code, "name": name,
    }
    with get_db_session(config["database"]["sqlite_path"]) as s:
        row = StockDiagnosis(code=code, name=name, trade_date="2026-09-25", action=action, score=score,
                             result_json=raw if raw is not None else json.dumps(result, ensure_ascii=False),
                             created_at=created)
        s.add(row)
        s.flush()
        return row.id


@pytest.fixture
def seeded(env):
    client, app, config = env
    now = datetime.now()
    ids = {
        "a": _add(config, "600519", "贵州茅台", "buy", 80, now - timedelta(hours=1)),
        "b": _add(config, "600519", "贵州茅台", "hold", 60, now - timedelta(days=5)),
        "c": _add(config, "601919", "中远海控", "sell", 30, now - timedelta(days=40)),
        "d": _add(config, "601919", "中远海控", "buy", 75, now - timedelta(days=2)),
    }
    return client, app, config, ids


def _pipeline(app):
    return app.state.pipeline


def test_empty(env):
    client, app, _ = env
    r = _pipeline(app).list_diagnoses()
    assert r["total"] == 0 and r["items"] == []
    assert client.get("/api/v1/stocks/diagnoses").json() == {"total": 0, "items": []}


def test_list_order_and_fields(seeded):
    _, app, _, ids = seeded
    r = _pipeline(app).list_diagnoses()
    # 默认 30 天：c 被过滤
    assert r["total"] == 3
    assert [i["id"] for i in r["items"]] == [ids["a"], ids["d"], ids["b"]]
    item = r["items"][0]
    for k in ("id", "code", "name", "trade_date", "action", "score", "summary", "created_at"):
        assert k in item
    assert len(item["created_at"]) == 16 and item["created_at"][10] == " "


def test_days_boundary(seeded):
    _, app, _, ids = seeded
    p = _pipeline(app)
    assert p.list_diagnoses(days=0)["total"] == 4
    assert p.list_diagnoses(days=-1)["total"] == 4
    assert p.list_diagnoses(days=1)["total"] == 1
    assert p.list_diagnoses(days=3)["total"] == 2
    assert p.list_diagnoses(days=60)["total"] == 4


@pytest.mark.parametrize("code", ["600519", "sh600519", "SH600519", "600519.SH"])
def test_code_filter_variants(seeded, code):
    _, app, _, _ = seeded
    r = _pipeline(app).list_diagnoses(code=code, days=0)
    assert r["total"] == 2 and {i["code"] for i in r["items"]} == {"600519"}


def test_action_filter(seeded):
    _, app, _, _ = seeded
    p = _pipeline(app)
    assert p.list_diagnoses(action="buy", days=0)["total"] == 2
    assert p.list_diagnoses(action="sell", days=0)["total"] == 1
    assert p.list_diagnoses(action="avoid", days=0)["total"] == 0
    assert p.list_diagnoses(code="601919", action="buy", days=0)["total"] == 1


def test_pagination(seeded):
    _, app, _, _ = seeded
    p = _pipeline(app)
    r = p.list_diagnoses(days=0, limit=2, offset=0)
    assert r["total"] == 4 and len(r["items"]) == 2
    r2 = p.list_diagnoses(days=0, limit=2, offset=2)
    assert r2["total"] == 4 and len(r2["items"]) == 2
    assert not {i["id"] for i in r["items"]} & {i["id"] for i in r2["items"]}
    r3 = p.list_diagnoses(days=0, limit=2, offset=100)
    assert r3["total"] == 4 and r3["items"] == []


def test_get_and_delete(seeded):
    _, app, _, ids = seeded
    p = _pipeline(app)
    d = p.get_diagnosis(ids["a"])
    assert d["code"] == "600519" and d["name"] == "贵州茅台" and d["result"]["one_sentence"] == "贵州茅台结论"
    assert p.get_diagnosis(99999) is None
    assert p.delete_diagnosis(ids["a"]) is True
    assert p.delete_diagnosis(ids["a"]) is False
    assert p.get_diagnosis(ids["a"]) is None


def test_corrupt_result_json(env):
    client, app, config = env
    i = _add(config, "600519", "贵州茅台", "buy", 50, datetime.now(), raw="{not json")
    d = _pipeline(app).get_diagnosis(i)  # 不抛异常
    assert d is not None and d["id"] == i
    assert client.get(f"/api/v1/stocks/diagnoses/{i}").status_code == 200
    client.get(f"/api/v1/stocks/diagnoses/{i}/markdown")  # 不应抛出


def test_api_list(seeded):
    client, _, _, _ = seeded
    r = client.get("/api/v1/stocks/diagnoses", params={"code": "sh600519", "days": 0, "limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2 and len(body["items"]) == 1
    assert client.get("/api/v1/stocks/diagnoses", params={"action": "sell", "days": 0}).json()["total"] == 1
    assert client.get("/api/v1/stocks/diagnoses", params={"limit": 50, "offset": 500}).json()["items"] == []


@pytest.mark.parametrize("limit", [0, 201, -1])
def test_api_limit_validation(seeded, limit):
    client, _, _, _ = seeded
    assert client.get("/api/v1/stocks/diagnoses", params={"limit": limit}).status_code == 422


def test_api_detail_delete(seeded):
    client, _, _, ids = seeded
    r = client.get(f"/api/v1/stocks/diagnoses/{ids['a']}")
    assert r.status_code == 200 and r.json()["result"]["action"] == "buy"
    assert client.get("/api/v1/stocks/diagnoses/99999").status_code == 404
    assert client.delete("/api/v1/stocks/diagnoses/99999").status_code == 404
    d = client.delete(f"/api/v1/stocks/diagnoses/{ids['a']}")
    assert d.status_code == 200 and d.json() == {"ok": True}
    assert client.get(f"/api/v1/stocks/diagnoses/{ids['a']}").status_code == 404


def test_api_markdown(seeded):
    client, _, _, ids = seeded
    r = client.get(f"/api/v1/stocks/diagnoses/{ids['a']}/markdown")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "AI 诊断" in r.text and "贵州茅台" in r.text
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment") and "filename*=UTF-8''" in cd
    cd.encode("latin-1")  # 头部必须是 ASCII/latin-1 安全（中文已百分号编码）
    assert quote("贵州茅台") in cd or "600519" in cd
    assert client.get("/api/v1/stocks/diagnoses/99999/markdown").status_code == 404


def test_api_image(seeded, monkeypatch):
    client, _, _, ids = seeded
    monkeypatch.setattr(report_image, "render_png", lambda html, width=760: PNG)
    monkeypatch.setattr(report_image, "render_markdown_image", lambda title, md, footer="", brand="", qr_url="": PNG)
    r = client.get(f"/api/v1/stocks/diagnoses/{ids['a']}/image")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    assert r.content == PNG
    assert client.get("/api/v1/stocks/diagnoses/99999/image").status_code == 404


def test_api_image_render_failure(seeded, monkeypatch):
    client, _, _, ids = seeded

    def boom(*a, **k):
        raise RuntimeError("no chromium")
    monkeypatch.setattr(report_image, "render_png", boom)
    monkeypatch.setattr(report_image, "render_markdown_image", boom)
    assert client.get(f"/api/v1/stocks/diagnoses/{ids['a']}/image").status_code == 503


def test_existing_routes_not_shadowed(seeded):
    client, _, _, _ = seeded
    r = client.get("/api/v1/stocks/600519/diagnosis")
    assert r.status_code == 200
    r = client.get("/api/v1/stocks/diagnoses")
    assert r.status_code == 200 and "total" in r.json()
