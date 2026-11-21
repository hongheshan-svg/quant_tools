"""实盘记账拒绝非有限数值：JSON 里的 1e400 会被解析成 inf，NaN/Infinity 常量也能解析。"""
import pytest

from tests.test_api import env  # noqa: F401

TRADE = {"trade_date": "2026-09-25", "code": "600519", "side": "buy", "price": 10.0, "quantity": 100}


def _raw(client, url, body: str, method: str = "post"):
    return client.request(method.upper(), url, content=body, headers={"content-type": "application/json"})


@pytest.mark.parametrize("field,value", [("price", "1e400"), ("price", "Infinity"), ("fee", "1e400"), ("fee", "NaN")])
def test_trade_rejects_non_finite_numbers(env, field, value):
    client, _, _ = env
    body = '{"trade_date":"2026-09-25","code":"600519","side":"buy","price":10,"quantity":100,"fee":0}'
    body = body.replace(f'"{field}":10' if field == "price" else f'"{field}":0', f'"{field}":{value}')
    response = _raw(client, "/api/v1/real/trades", body)
    assert response.status_code == 422, response.text
    assert client.get("/api/v1/real").json()["trades"] == []


@pytest.mark.parametrize("method,url,body", [
    ("put", "/api/v1/real/cash", '{"cash":1e400}'),
    ("post", "/api/v1/real/cash-flows", '{"flow_date":"2026-09-25","direction":"in","amount":1e400}'),
    ("put", "/api/v1/real/plans/600519", '{"stop_loss":1e400}'),
    ("post", "/api/v1/real/actions", '{"ex_date":"2026-09-25","code":"600519","action":"dividend","cash":1e400}'),
    ("post", "/api/v1/real/actions", '{"ex_date":"2026-09-25","code":"600519","plan":{"cash_per_10":1e400}}'),
])
def test_other_ledger_bodies_reject_non_finite(env, method, url, body):
    client, _, _ = env
    response = _raw(client, url, body, method)
    assert response.status_code == 422, response.text
    assert "detail" in response.json()


def test_service_rejects_overflowing_import_numbers(env):
    from src.services.real_portfolio import RealPortfolioService
    _, _, config = env
    svc = RealPortfolioService(config)
    assert svc.add_trade("2026-09-25", "600519", "buy", float("inf"), 100)["ok"] is False
    assert svc.add_trade("2026-09-25", "600519", "buy", 10.0, 100, fee=float("nan"))["ok"] is False
    assert svc.add_trade("2026-09-25", "600519", "buy", 10.0, 100)["ok"] is True
