from __future__ import annotations

import types

import pandas as pd

from src.collectors import global_news as global_news_mod
from src.collectors import us_earnings as us_earnings_mod
from src.collectors.us_earnings import USEarningsCollector
from src.services import collector_orchestrator as orchestrator_mod


class _DummyRealtimeAI:
    def __init__(self, config):
        self.config = config

    def process_cailianshe_stream(self, items):
        return 0


def _make_orchestrator(monkeypatch, collect_max_attempts: int = 2):
    monkeypatch.setattr(orchestrator_mod, "init_db", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator_mod, "RealtimeNewsAIProcessor", _DummyRealtimeAI)
    monkeypatch.setattr(orchestrator_mod.time, "sleep", lambda *_args, **_kwargs: None)
    config = {
        "database": {"sqlite_path": "data/test_quant.db"},
        "desktop": {
            "enable_realtime_ai": False,
            "collect_max_attempts": collect_max_attempts,
        },
    }
    return orchestrator_mod.CollectorOrchestrator(config=config)


def _full_news_counts() -> dict[str, int]:
    return {
        "cailianshe": 1,
        "xueqiu": 1,
        "jiuyan": 1,
        "hot_topics": 1,
        "weibo": 1,
        "douyin": 1,
        "toutiao": 1,
    }


def _ok_market() -> dict[str, str]:
    return {
        "realtime_quotes": "ok",
        "limit_up_pool": "ok",
        "dragon_tiger": "ok",
        "northbound_flow": "ok",
    }


def test_collect_all_retry_until_required_sources_ready(monkeypatch):
    orchestrator = _make_orchestrator(monkeypatch, collect_max_attempts=3)
    calls = {"news": 0}

    def fake_collect_news():
        calls["news"] += 1
        news = _full_news_counts()
        if calls["news"] == 1:
            news["weibo"] = 0
        return news

    monkeypatch.setattr(orchestrator, "collect_news_parallel", fake_collect_news)
    monkeypatch.setattr(orchestrator, "collect_market_parallel", lambda: _ok_market())
    monkeypatch.setattr(orchestrator, "collect_market_overview", lambda: {})
    monkeypatch.setattr(orchestrator, "_collect_global_news", lambda: 4)
    monkeypatch.setattr(orchestrator, "_collect_us_earnings", lambda: 6)

    result = orchestrator.collect_all()

    assert result["all_sources_ok"] is True
    assert result["missing_sources"] == []
    assert result["collect_attempts"] == 2
    assert calls["news"] == 2


def test_collect_all_marks_failure_when_attempts_exhausted(monkeypatch):
    orchestrator = _make_orchestrator(monkeypatch, collect_max_attempts=2)
    monkeypatch.setattr(orchestrator, "collect_news_parallel", lambda: _full_news_counts())
    monkeypatch.setattr(orchestrator, "collect_market_parallel", lambda: _ok_market())
    monkeypatch.setattr(orchestrator, "collect_market_overview", lambda: {})
    monkeypatch.setattr(orchestrator, "_collect_global_news", lambda: 0)
    monkeypatch.setattr(orchestrator, "_collect_us_earnings", lambda: 6)

    result = orchestrator.collect_all()

    assert result["all_sources_ok"] is False
    assert "global_news" in result["missing_sources"]
    assert result["collect_attempts"] == 2


def test_collect_global_news_requires_all_core_sources(monkeypatch):
    orchestrator = _make_orchestrator(monkeypatch, collect_max_attempts=1)

    class _GlobalNewsMissing:
        def __init__(self, config):
            self.config = config

        def collect(self):
            return [
                {"source": "cailianshe_global"},
                {"source": "wallstreetcn"},
                {"source": "jin10"},
            ]

    monkeypatch.setattr(global_news_mod, "GlobalNewsCollector", _GlobalNewsMissing)
    assert orchestrator._collect_global_news() == 0

    class _GlobalNewsFull:
        def __init__(self, config):
            self.config = config

        def collect(self):
            return [
                {"source": "cailianshe_global"},
                {"source": "wallstreetcn"},
                {"source": "jin10"},
                {"source": "eastmoney_global"},
            ]

    monkeypatch.setattr(global_news_mod, "GlobalNewsCollector", _GlobalNewsFull)
    assert orchestrator._collect_global_news() == 4


def test_collect_us_hot_and_estimate_outputs_two_groups(monkeypatch):
    collector = USEarningsCollector(config={})
    monkeypatch.setattr(
        USEarningsCollector,
        "US_A_MAPPING",
        {
            "NVDA": {"name": "NVIDIA", "a_share_sectors": ["AI"], "a_share_stocks": ["TEST"]},
            "AAPL": {"name": "Apple", "a_share_sectors": ["Consumer"], "a_share_stocks": ["TEST2"]},
        },
    )
    monkeypatch.setattr(
        collector,
        "_fetch_sina_quotes",
        lambda symbols: {
            "nvda": {"price": 100.0, "change_pct": 4.2},
            "aapl": {"price": 180.0, "change_pct": 1.1},
        },
    )

    saved_items: list[dict] = []

    def fake_save(_db_path, items, _log_label):
        saved_items.extend(items)
        return len(items)

    monkeypatch.setattr(collector, "_save_global_news_items", fake_save)
    hot_count, estimate_count = collector._collect_us_24h_hot_and_estimate("dummy.db")

    assert hot_count > 0
    assert estimate_count > 0
    assert any(item.get("source") == "sina_us_24h" for item in saved_items)
    assert any(item.get("source") == "sina_us_estimate" for item in saved_items)
    collector.close()


def test_collect_earnings_news_fallback_generates_minimum_items(monkeypatch):
    collector = USEarningsCollector(config={})
    monkeypatch.setattr(
        USEarningsCollector,
        "US_A_MAPPING",
        {
            "NVDA": {"name": "NVIDIA", "a_share_sectors": ["AI"], "a_share_stocks": ["TEST"]},
        },
    )

    fake_ak = types.SimpleNamespace(stock_info_global_em=lambda: pd.DataFrame())
    monkeypatch.setattr(us_earnings_mod, "ak", fake_ak)

    class _Resp:
        status_code = 500

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: _Resp())
    monkeypatch.setattr(
        collector,
        "_fetch_sina_quotes",
        lambda symbols: {"nvda": {"price": 120.0, "change_pct": 3.5}},
    )

    saved_items: list[dict] = []

    def fake_save(_db_path, items, _log_label):
        saved_items.extend(items)
        return len(items)

    monkeypatch.setattr(collector, "_save_global_news_items", fake_save)
    earnings_count = collector._collect_earnings_news("dummy.db")

    assert earnings_count == 1
    assert any(item.get("source") == "sina_us_earnings_est" for item in saved_items)
    collector.close()


def test_market_collection_skipped_before_market_data_ready(monkeypatch):
    from src import trading_calendar

    orchestrator = _make_orchestrator(monkeypatch)
    monkeypatch.setattr(trading_calendar, "market_data_ready", lambda now=None: False)
    monkeypatch.setattr("src.collectors.stock_data.StockDataCollector.__init__", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not collect")))
    assert orchestrator.collect_market_parallel() == {
        "realtime_quotes": "skipped", "limit_up_pool": "skipped", "dragon_tiger": "skipped", "northbound_flow": "skipped",
    }
    assert orchestrator._check_missing_sources({"market": orchestrator.collect_market_parallel()}) == [
        f"news.{s}" for s in orchestrator.required_news_sources
    ] + ["global_news", "us_earnings"]  # 跳过的行情不算缺失
