from __future__ import annotations

import pytest

from src.analyzers.attribution import normalize_attribution

KEYS = ("technical", "news", "fundamentals", "market")


def _raw(t=None, n=None, f=None, m=None, bull="", bear=""):
    return {"technical": t, "news": n, "fundamentals": f, "market": m, "strongest_bullish": bull, "strongest_bearish": bear}


def _sum(out):
    return sum(out[k] for k in KEYS)


def test_percent_strings_and_numbers():
    out = normalize_attribution(_raw("40%", "30", 20, 10.0, "封板早", "高位分歧"))
    assert [out[k] for k in KEYS] == [40, 30, 20, 10]
    assert out["strongest_bullish"] == "封板早" and out["strongest_bearish"] == "高位分歧"


def test_scale_to_exactly_100():
    out = normalize_attribution(_raw(50, 50, 50, 50))
    assert [out[k] for k in KEYS] == [25, 25, 25, 25]
    out = normalize_attribution(_raw(1, 1, 1, 0))
    assert _sum(out) == 100 and all(isinstance(out[k], int) for k in KEYS)
    assert out["market"] == 0 and sorted(out[k] for k in KEYS[:3]) == [33, 33, 34]
    out = normalize_attribution(_raw(70, 30, 20, 10))  # 合计 130
    assert _sum(out) == 100 and out["technical"] > out["news"] > out["fundamentals"] >= out["market"]


def test_largest_remainder_random_sums():
    for vals in [(33.3, 33.3, 33.3, 0.1), (7, 11, 13, 17), (99, 99, 99, 99), (0.5, 0.5, 0.5, 0.5)]:
        assert _sum(normalize_attribution(_raw(*vals))) == 100


def test_negative_becomes_zero_and_over_100_capped():
    out = normalize_attribution(_raw(-5, 50, 30, 20))
    assert out["technical"] == 0 and _sum(out) == 100
    out = normalize_attribution(_raw(150, 0, 0, 0))
    assert [out[k] for k in KEYS] == [100, 0, 0, 0]


def test_all_zero_stays_zero():
    out = normalize_attribution(_raw(0, 0, 0, 0, "x"))
    assert [out[k] for k in KEYS] == [0, 0, 0, 0]


def test_none_values_no_scaling_only_round():
    out = normalize_attribution(_raw(40.6, None, "30%", 20, "a"))
    assert out["news"] is None
    assert (out["technical"], out["fundamentals"], out["market"]) == (41, 30, 20)


def test_unparseable_becomes_none():
    out = normalize_attribution(_raw("很高", 50, 50, 0, "a"))
    assert out["technical"] is None and out["news"] == 50


def test_text_stripped_and_truncated():
    out = normalize_attribution(_raw(25, 25, 25, 25, "  " + "多" * 100 + "  ", None))
    assert out["strongest_bullish"] == "多" * 60
    assert out["strongest_bearish"] == ""
    out = normalize_attribution({"technical": 25, "news": 25, "fundamentals": 25, "market": 25})
    assert out["strongest_bullish"] == "" and out["strongest_bearish"] == ""


@pytest.mark.parametrize("raw", [None, "abc", 5, [], [1, 2], {}, _raw(), {"technical": "x", "news": None}])
def test_invalid_returns_empty(raw):
    assert normalize_attribution(raw) == {}


def test_only_text_kept():
    out = normalize_attribution(_raw(bull="只有文字"))
    assert out and out["strongest_bullish"] == "只有文字" and out["technical"] is None
