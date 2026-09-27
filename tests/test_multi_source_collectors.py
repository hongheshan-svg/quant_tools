from __future__ import annotations

import sys
import types

import pandas as pd

from src.collectors import hot_topics as hot_mod
from src.collectors import jiuyan as jy_mod
from src.collectors import xueqiu as xq_mod
from src.collectors import em_client as em_mod


class _DummyEMForHotConcept:
    def request_json(self, url, params=None, timeout=8000, referer=None):
        return {
            "data": {
                "diff": [
                    {"f14": "中性概念", "f3": 0, "f8": 1.23},
                ]
            }
        }

    def stock_board_concept_name_em(self):
        return pd.DataFrame()


class _DummyEMForJiuyan:
    def stock_zt_pool_em(self, date):
        return pd.DataFrame()

    def stock_board_concept_name_em(self):
        return pd.DataFrame([{"板块名称": "平盘概念", "涨跌幅": 0}])


def test_hot_topics_keep_zero_change_concept(monkeypatch):
    monkeypatch.setattr(hot_mod, "get_em_client", lambda: _DummyEMForHotConcept())
    collector = hot_mod.HotTopicCollector(config={})

    items = collector._collect_eastmoney_hot_concept()

    assert items
    assert items[0]["title"].startswith("热门概念#1: 中性概念")
    assert "涨幅+0.00%" in items[0]["title"]


def test_jiuyan_keep_zero_change_concept(monkeypatch):
    monkeypatch.setattr(em_mod, "get_em_client", lambda: _DummyEMForJiuyan())
    collector = jy_mod.JiuyanCollector(config={})

    items = collector._collect_eastmoney_limitup_review()
    concept_items = [x for x in items if x.get("category") == "题材解读"]

    assert concept_items
    assert "涨幅+0.00%" in concept_items[0]["title"]


def test_xueqiu_akshare_non_numeric_change_pct(monkeypatch):
    fake_ak = types.SimpleNamespace(
        stock_hot_rank_em=lambda: pd.DataFrame(
            [
                {
                    "代码": "sz000001",
                    "股票名称": "平安银行",
                    "当前排名": 1,
                    "最新价": 10.5,
                    "涨跌幅": "--",
                }
            ]
        )
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    monkeypatch.setattr(xq_mod.XueqiuCollector, "_init_cookies", lambda self: None)
    collector = xq_mod.XueqiuCollector(config={})

    items = collector._collect_from_akshare()

    assert items
    assert items[0]["stock_code"] == "000001"
    assert "涨跌幅0.00%" in items[0]["content"]
