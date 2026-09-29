"""系统错误告警：report_error 的冷却、开关、截断，以及调度任务异常时的上报。全部离线。"""

from __future__ import annotations

import pytest

from src import notifier
from src import scheduler as sched
from src.services import system_alerts


@pytest.fixture
def sent(monkeypatch):
    """记录 broadcast 调用；同时替换 notifier 与 system_alerts 模块上的引用（兼容两种导入方式）。"""
    calls: list[dict] = []

    def fake_broadcast(config, title, content, kind=None):
        calls.append({"title": title, "content": content, "kind": kind})
        return {"wechat": True}

    def fake_enabled(config, kind=None):
        return ["wechat"]

    for mod in (notifier, system_alerts):
        monkeypatch.setattr(mod, "broadcast", fake_broadcast, raising=False)
        monkeypatch.setattr(mod, "enabled_channels", fake_enabled, raising=False)
    monkeypatch.delenv("QUANT_NO_NOTIFY", raising=False)
    system_alerts.reset_state()
    yield calls
    system_alerts.reset_state()


def test_kind_registered():
    assert "system_error" in notifier.MESSAGE_KINDS


def test_report_error_pushes_once_with_title(sent):
    assert system_alerts.report_error({}, "每日分析", RuntimeError("数据库锁定")) is True
    assert len(sent) == 1
    assert sent[0]["kind"] == "system_error"
    assert "系统错误" in sent[0]["title"] and "每日分析" in sent[0]["title"]
    assert "数据库锁定" in sent[0]["content"]


def test_cooldown_same_source_and_independent_sources(sent):
    assert system_alerts.report_error({}, "A任务", "boom") is True
    assert system_alerts.report_error({}, "A任务", "boom again") is False   # 冷却期内
    assert system_alerts.report_error({}, "B任务", "boom") is True          # 不同来源互不影响
    assert len(sent) == 2
    system_alerts.reset_state()
    assert system_alerts.report_error({}, "A任务", "boom") is True
    assert len(sent) == 3


def test_custom_cooldown_zero_allows_repeat(sent):
    assert system_alerts.report_error({}, "C任务", "x", cooldown_minutes=0) is True
    assert system_alerts.report_error({}, "C任务", "x", cooldown_minutes=0) is True
    assert len(sent) == 2


def test_config_cooldown_used(sent):
    config = {"notifier": {"system_error": {"cooldown_minutes": 0}}}
    assert system_alerts.report_error(config, "D任务", "x") is True
    assert system_alerts.report_error(config, "D任务", "x") is True


def test_disabled_does_not_push(sent):
    config = {"notifier": {"system_error": {"enabled": False}}}
    assert system_alerts.report_error(config, "E任务", "x") is False
    assert sent == []


def test_no_channel_does_not_push(monkeypatch, sent):
    for mod in (notifier, system_alerts):
        monkeypatch.setattr(mod, "enabled_channels", lambda config, kind=None: [], raising=False)
        monkeypatch.setattr(mod, "broadcast", lambda config, title, content, kind=None: {}, raising=False)
    assert system_alerts.report_error({}, "F任务", "x") is False


def test_long_error_truncated(sent):
    system_alerts.report_error({}, "G任务", "X" * 3000)
    assert len(sent) == 1
    content = sent[0]["content"]
    assert content.count("X") <= 500
    assert "X" * 100 in content


# ---------- 调度任务异常上报 ----------

@pytest.fixture
def reported(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(system_alerts, "report_error", lambda *a, **k: calls.append((a, k)) or True)
    monkeypatch.setattr(sched, "_skip_non_trade_day", lambda config, name: False)
    return calls


def _assert_reported(calls, marker: str):
    assert len(calls) >= 1
    args, kwargs = calls[0]
    flat = list(args) + list(kwargs.values())
    assert any(marker in str(x) for x in flat), flat
    assert any(isinstance(x, str) and x and marker not in x for x in flat)  # 含任务来源名


def test_daily_report_market_review_error_reported(reported, monkeypatch):
    from src.services import market_review

    def boom(self, *a, **k):
        raise RuntimeError("review-boom")

    monkeypatch.setattr(market_review.MarketReviewService, "generate", boom)
    config = {"notifier": {"daily_report_enabled": False}, "market_review": {"enabled": True}}
    sched._run_daily_report(config)
    _assert_reported(reported, "review-boom")


def test_self_learning_error_reported(reported, monkeypatch):
    from src.services import self_learning

    def boom(self, *a, **k):
        raise RuntimeError("learn-boom")

    monkeypatch.setattr(self_learning.SelfLearningService, "run_daily_learning", boom)
    monkeypatch.setattr(self_learning.SelfLearningService, "__init__", lambda self, config=None: None)
    sched._run_self_learning({})
    _assert_reported(reported, "learn-boom")


def test_watchlist_report_error_reported(reported, monkeypatch):
    from src.services import watchlist_report

    def boom(self, *a, **k):
        raise RuntimeError("watch-boom")

    monkeypatch.setattr(watchlist_report.WatchlistReportService, "run", boom)
    monkeypatch.setattr(watchlist_report.WatchlistReportService, "__init__", lambda self, config=None, **k: None)
    sched._run_watchlist_report({"watchlist": {"daily_report": True}})
    _assert_reported(reported, "watch-boom")
