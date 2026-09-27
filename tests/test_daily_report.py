from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import date, datetime

from src import notifier as notifier_mod
from src import scheduler as scheduler_mod
from src import trading_calendar
from src.collectors.stock_info import StockInfoCollector
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, TradeOrder, TradeSignal
from src.notifier import broadcast, enabled_channels
from src.notifier import feishu as feishu_mod
from src.notifier import wechat as wechat_mod
from src.notifier.base import paged_titles, split_by_bytes
from src.notifier.feishu import FeishuNotifier
from src.notifier.wechat import WeChatNotifier
from src.services.daily_report import DailyReportService
from src.trading.constants import ORDER_STATUS_PENDING_CONFIRM

TODAY = date.today().strftime("%Y-%m-%d")


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


# ---------- 消息拆分 ----------

def test_split_by_bytes_respects_limit_and_keeps_content():
    content = "\n".join(f"第{i}行：比亚迪(002594) 买入 4000股" for i in range(200))
    chunks = split_by_bytes(content, 1000)
    assert len(chunks) > 1
    assert all(len(c.encode("utf-8")) <= 1000 for c in chunks)
    assert "\n".join(chunks) == content

    long_line = "涨" * 1000  # 单行 3000 字节，需要硬切
    assert all(len(c.encode("utf-8")) <= 1000 for c in split_by_bytes(long_line, 1000))
    assert "".join(split_by_bytes(long_line, 1000)) == long_line

    assert paged_titles("日报", 1) == ["日报"]
    assert paged_titles("日报", 2) == ["日报（1/2）", "日报（2/2）"]


def test_wechat_splits_long_messages_under_4096_bytes(monkeypatch):
    sent = []
    monkeypatch.setattr(wechat_mod.httpx, "post", lambda url, json, timeout: sent.append(json) or _Resp({"errcode": 0}))
    config = {"notifier": {"wechat": {"enabled": True, "webhook_url": "https://example.invalid/hook"}}}

    assert WeChatNotifier(config).send("日报", "\n".join(["信号行" * 30] * 100)) is True
    assert len(sent) > 1
    assert all(len(p["markdown"]["content"].encode("utf-8")) <= 4096 for p in sent)
    assert sent[0]["markdown"]["content"].startswith(f"## 日报（1/{len(sent)}）")


def test_feishu_signature_and_card(monkeypatch):
    sent = []
    monkeypatch.setattr(feishu_mod.time, "time", lambda: 1700000000)
    monkeypatch.setattr(feishu_mod.httpx, "post", lambda url, json, timeout: sent.append(json) or _Resp({"code": 0}))
    config = {"notifier": {"feishu": {"enabled": True, "webhook_url": "https://example.invalid/hook", "secret": "s3cret"}}}

    assert FeishuNotifier(config).send("日报", "**内容**") is True
    expected = base64.b64encode(hmac.new(b"1700000000\ns3cret", digestmod=hashlib.sha256).digest()).decode()
    assert (sent[0]["timestamp"], sent[0]["sign"]) == ("1700000000", expected)
    assert sent[0]["card"]["elements"][0] == {"tag": "markdown", "content": "**内容**"}


def test_broadcast_only_enabled_channels_and_isolates_failures(monkeypatch):
    config = {"notifier": {
        "wechat": {"enabled": True, "webhook_url": "https://example.invalid/a"},
        "dingtalk": {"enabled": True, "webhook_url": ""},  # 没配 URL 视为未启用
        "feishu": {"enabled": True, "webhook_url": "https://example.invalid/b"},
    }}
    assert enabled_channels(config) == ["wechat", "feishu"]

    class _Boom:
        def __init__(self, config):
            pass

        def send(self, title, content):
            raise RuntimeError("network down")

    class _Ok(_Boom):
        def send(self, title, content):
            return True

    monkeypatch.setitem(notifier_mod.NOTIFIERS, "wechat", _Boom)
    monkeypatch.setitem(notifier_mod.NOTIFIERS, "feishu", _Ok)
    assert broadcast(config, "t", "c") == {"wechat": False, "feishu": True}


# ---------- 日报内容 ----------

def _seed(tmp_path, monkeypatch) -> dict:
    db_path = str(tmp_path / "report.db")
    _reset_db_engine()
    init_db(db_path)
    monkeypatch.setattr(StockInfoCollector, "refresh_if_stale", lambda self, path, max_age_hours=24: 0)
    with get_db_session(db_path) as session:
        session.add(StockDaily(code="002594", name="比亚迪", trade_date=TODAY, open=20.0, close=20.0))
        session.add(TradeSignal(code="002594", name="比亚迪", signal_date=TODAY, signal_type="premarket", ai_verdict="买入",
                                composite_score=90, entry_price=20.1, stop_loss_price=19.0, target_price=23.0,
                                reason="##TYPE:龙头##TIME:开盘##SRC:热点驱动##固态电池政策催化，板块龙头",
                                created_at=datetime.now()))
        session.add(TradeSignal(code="600519", name="贵州茅台", signal_date=TODAY, signal_type="buy",
                                composite_score=75, reason="综合75分 | 首板", created_at=datetime.now()))
        session.add(TradeOrder(id="o1", signal_date=TODAY, code="002594", name="比亚迪", side="buy", order_type="limit",
                               price=20.1, quantity=4000, amount=80400, status=ORDER_STATUS_PENDING_CONFIRM,
                               broker="paper", idempotency_key="k1"))
    return {"database": {"sqlite_path": db_path}, "risk": {}, "trading": {}}


def test_report_contains_all_sections(tmp_path, monkeypatch):
    config = _seed(tmp_path, monkeypatch)
    overview = {
        "sh_index": "3250.12", "sh_change_pct": 0.85, "sz_index": "10521.36", "sz_change_pct": 1.02,
        "up_count": 3920, "down_count": 1349, "limit_up_count": 155, "limit_down_count": 3,
        "total_amount_yi": 12345, "northbound_net_yi": 35.2, "market_emotion": "偏暖",
        "top_sectors": [{"name": "互联网服务", "pct": 3.1}, {"name": "小金属", "pct": 2.5}],
    }
    title, content = DailyReportService(config).build(overview=overview)

    assert title == f"A股量化日报 {TODAY}"
    assert "上证 3250.12（+0.85%）" in content
    assert "涨停 155 / 跌停 3" in content and "北向 +35.2亿" in content
    assert "领涨板块：互联网服务、小金属" in content
    assert "**比亚迪(002594)** AI预测·买入 | 买入20.10 止损19.00 目标23.00" in content
    assert "固态电池政策催化，板块龙头" in content and "##TYPE" not in content  # 去掉结构标记
    assert "**贵州茅台(600519)** 评分信号 75分" in content
    assert "买入 比亚迪(002594) 4000股 @20.10" in content
    assert "### 模拟盘" in content
    _reset_db_engine()


def test_push_skips_when_no_channel(tmp_path, monkeypatch):
    config = _seed(tmp_path, monkeypatch)
    assert DailyReportService(config).push() == {"pushed": False, "reason": "未启用任何推送渠道"}
    _reset_db_engine()


def test_scheduler_report_job_respects_trade_day_and_switch(monkeypatch):
    calls = []
    monkeypatch.setattr(trading_calendar, "load", lambda db_path, refresh=True: True)
    monkeypatch.setattr(DailyReportService, "push", lambda self: calls.append(1) or {"pushed": True})

    monkeypatch.setattr(trading_calendar, "is_trade_day", lambda d=None: False)
    scheduler_mod._run_daily_report({})
    assert calls == []

    monkeypatch.setattr(trading_calendar, "is_trade_day", lambda d=None: True)
    scheduler_mod._run_daily_report({"notifier": {"daily_report_enabled": False}})
    assert calls == []
    scheduler_mod._run_daily_report({})
    assert calls == [1]
