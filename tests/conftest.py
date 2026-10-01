"""测试公共 fixture。

autouse 的 _fixed_market_phase 把 market_phase.current_phase 固定为「盘后」且
effective_daily_bar_date=None（跳过行情过时判断），避免已有诊断测试随运行时间漂移。
测试阶段逻辑本身时，用 market_phase._real_current_phase（真实函数的引用，导入时保存）
并传入 now 和 trading_calendar._set_days(...) 指定的日历；或在测试里自行 monkeypatch current_phase。
"""

from __future__ import annotations

import pytest

from src.services import market_phase

# 真实函数的引用：conftest 导入时（patch 之前）保存，供阶段逻辑测试使用
market_phase._real_current_phase = market_phase.current_phase


def fixed_postmarket_phase(now=None) -> dict:
    return {
        "phase": "postmarket", "label": "盘后", "now": "2026-01-02 16:00", "is_trading_day": True,
        "is_partial_bar": False, "minutes_to_open": None, "minutes_to_close": None,
        "effective_daily_bar_date": None,
    }


@pytest.fixture(autouse=True)
def _fixed_market_phase(monkeypatch):
    monkeypatch.setattr(market_phase, "current_phase", fixed_postmarket_phase)
    yield
