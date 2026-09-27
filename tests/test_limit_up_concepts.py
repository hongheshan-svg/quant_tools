from __future__ import annotations

import pandas as pd
import pytest

from src.collectors import limit_up_reasons as lur
from src.collectors import source_chain as sc
from src.collectors import stock_data as sd_mod
from src.collectors.limit_up_reasons import fetch_ths_limit_up_reasons, split_concepts
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import LimitUpStock


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _page(date: str, items: list[dict], total: int) -> dict:
    return {"status_code": 0, "status_msg": "success", "data": {"date": date, "info": items, "page": {"total": total}}}


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def test_split_concepts():
    assert split_concepts("海峡两岸+工程机械＋ 盾构机+海峡两岸") == ["海峡两岸", "工程机械", "盾构机"]
    assert split_concepts(None) == [] and split_concepts("") == []


def test_fetch_reasons_paginates(monkeypatch):
    first = [{"code": f"{600000 + i}", "reason_type": "机器人+减速器"} for i in range(200)]
    second = [{"code": "000001", "reason_type": "海峡两岸"}, {"code": "000002", "reason_type": ""}]
    pages = {1: _page("20260924", first, 202), 2: _page("20260924", second, 202)}
    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append(params["page"])
        assert params["date"] == "20260924"
        return _Resp(pages[params["page"]])

    monkeypatch.setattr(lur.httpx, "get", fake_get)
    reasons = fetch_ths_limit_up_reasons("2026-09-24")
    assert calls == [1, 2]
    assert len(reasons) == 201 and reasons["000001"] == "海峡两岸" and "000002" not in reasons


def test_fetch_reasons_rejects_wrong_date_and_errors(monkeypatch):
    monkeypatch.setattr(lur.httpx, "get", lambda *a, **k: _Resp(_page("20260923", [{"code": "1", "reason_type": "x"}], 1)))
    with pytest.raises(RuntimeError, match="返回了 20260923"):
        fetch_ths_limit_up_reasons("2026-09-24")

    monkeypatch.setattr(lur.httpx, "get", lambda *a, **k: _Resp({"status_code": 1, "status_msg": "参数错误"}))
    with pytest.raises(RuntimeError, match="参数错误"):
        fetch_ths_limit_up_reasons("2026-09-24")

    monkeypatch.setattr(lur.httpx, "get", lambda *a, **k: _Resp(_page("20260925", [], 0)))
    assert fetch_ths_limit_up_reasons("2026-09-25") == {}  # 休市日没有涨停


class _FakeEM:
    def stock_zt_pool_em(self, date):
        return pd.DataFrame([
            {"代码": "600815", "名称": "厦工股份", "最新价": 5.0, "涨跌幅": 9.9, "连板数": 2, "所属行业": "工程机械"},
            {"代码": "000001", "名称": "平安银行", "最新价": 10.0, "涨跌幅": 10.0, "连板数": 1, "所属行业": "银行"},
        ])

    def stock_zt_pool_strong_em(self, date):
        return pd.DataFrame()


@pytest.fixture
def collector(tmp_path, monkeypatch):
    sc._breakers.clear()
    sc._last_good.clear()
    db_path = str(tmp_path / "pool.db")
    _reset_db_engine()
    init_db(db_path)
    monkeypatch.setattr(sd_mod, "get_em_client", lambda: _FakeEM())
    yield sd_mod.StockDataCollector({"database": {"sqlite_path": db_path}}), db_path
    sc._breakers.clear()
    sc._last_good.clear()
    _reset_db_engine()


def test_limit_up_pool_stores_concepts(collector, monkeypatch):
    stock_collector, db_path = collector
    monkeypatch.setattr(sd_mod, "fetch_ths_limit_up_reasons", lambda d: {"600815": "海峡两岸+工程机械"})
    stock_collector._collect_limit_up_pool("2026-09-24", db_path)

    with get_db_session(db_path) as session:
        rows = {r.code: (r.concepts, r.reason) for r in session.query(LimitUpStock).all()}
    assert rows["600815"] == ("海峡两岸+工程机械", "海峡两岸+工程机械")  # 题材优先作为涨停原因
    assert rows["000001"] == ("", "银行")


def test_limit_up_pool_saved_even_if_ths_fails(collector, monkeypatch):
    stock_collector, db_path = collector
    monkeypatch.setattr(sc.time, "sleep", lambda s: None)

    def boom(d):
        raise ConnectionError("ths down")

    monkeypatch.setattr(sd_mod, "fetch_ths_limit_up_reasons", boom)
    stock_collector._collect_limit_up_pool("2026-09-24", db_path)
    with get_db_session(db_path) as session:
        rows = {r.code: (r.concepts, r.reason) for r in session.query(LimitUpStock).all()}
    assert rows == {"600815": ("", "2连板 | 工程机械"), "000001": ("", "银行")}


def test_backfill_concepts_for_existing_rows(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from scripts import fetch_history as fh
    from src.database.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'hist.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(LimitUpStock(code="600815", trade_date="2026-09-24", sector="工程机械", reason="2连板 | 工程机械"))
        session.add(LimitUpStock(code="000001", trade_date="2026-09-24", sector="银行", reason="银行"))
        session.add(LimitUpStock(code="300001", trade_date="2026-09-23", sector="软件", reason="自定义原因", concepts="已有"))
        session.commit()

    calls = []
    monkeypatch.setattr(fh, "fetch_ths_limit_up_reasons", lambda d: calls.append(d) or {"600815": "海峡两岸+工程机械"})
    monkeypatch.setattr(fh.time, "sleep", lambda s: None)
    assert fh.fetch_limit_up_concepts(engine, "2026-09-01", "2026-09-30") == 1
    assert calls == ["2026-09-24"]  # 09-23 已有题材，不重复请求
    with Session(engine) as session:
        row = session.query(LimitUpStock).filter(LimitUpStock.code == "600815").one()
        assert (row.concepts, row.reason) == ("海峡两岸+工程机械", "海峡两岸+工程机械")
    engine.dispose()
