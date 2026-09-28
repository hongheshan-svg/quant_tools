"""财联社采集：签名的 roll list 接口为主，旧接口兜底（不联网）"""

import hashlib
import time
import urllib.parse

from src.collectors import cailianshe
from src.collectors.cailianshe import CailiansheCollector, sign_params


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _item(i):
    return {"id": 1000 + i, "ctime": int(time.time()) - i * 60, "level": "B", "title": f"标题{i}", "content": f"【标题{i}】财联社电报内容{i}"}


def test_sign_params_matches_web_algorithm():
    params = {"rn": 50, "app": "CailianpressWeb", "os": "web"}
    signed = sign_params(params)
    query = "app=CailianpressWeb&os=web&rn=50"
    assert urllib.parse.urlencode(sorted(params.items())) == query
    assert signed["sign"] == hashlib.md5(hashlib.sha1(query.encode()).hexdigest().encode()).hexdigest()
    assert {k: v for k, v in signed.items() if k != "sign"} == params


def test_collect_uses_signed_roll_list(monkeypatch):
    calls = []

    def fake_fetch(self, url, params=None, max_retries=3, **kwargs):
        calls.append((url, params))
        return FakeResponse({"errno": 0, "data": {"roll_data": [_item(1), _item(2)]}})

    monkeypatch.setattr(CailiansheCollector, "fetch_url", fake_fetch)
    records = CailiansheCollector({}).collect()
    assert len(records) == 2
    assert calls[0][0] == CailiansheCollector.ROLL_LIST_URL
    assert calls[0][1]["rn"] == 50 and "sign" in calls[0][1]


def test_collect_falls_back_when_roll_list_rejects(monkeypatch):
    def fake_fetch(self, url, params=None, max_retries=3, **kwargs):
        if url == CailiansheCollector.ROLL_LIST_URL:
            return FakeResponse({"errno": "10012", "msg": "签名错误"})
        if url == CailiansheCollector.TELEGRAPH_LIST_URL:
            return FakeResponse({"data": {"roll_data": [_item(3)]}})
        return None

    monkeypatch.setattr(CailiansheCollector, "fetch_url", fake_fetch)
    monkeypatch.setattr(cailianshe.CailiansheCollector, "_collect_from_web", lambda self: [])
    records = CailiansheCollector({}).collect()
    assert len(records) == 1
