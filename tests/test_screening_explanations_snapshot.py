"""解释的零值、未知发布日期、事实/推断分离以及无联网逐条件检查。"""

from types import SimpleNamespace
import pytest
from src.strategy.screener import StrategyScreener, Pick
from src.strategy.snapshot_diagnostics import diagnose, validate_snapshot
from src.strategy.screening_explanations import explanations
from src.services.research_artifact import build_context_pack
from tests.test_screener import config  # noqa: F401


def test_explanations_do_not_invent_recent_catalysts_and_preserve_zero():
    pick = Pick("600001", "测试", ["test"], ["测试规则"], 60, ["规则命中"], [60], 10, 0, 1, True)
    pick.context_pack = build_context_pack({"code": "600001", "quote": {"close": 10, "trade_date": "2026-09-21"}, "flow": {"net_inflow": 0, "trade_date": "2026-09-21", "source": "fixture"}, "news_evidence": [{"title": "未知发布时间"}, {"title": "旧消息", "date": "2026-08-01"}]})
    selected, now = explanations(pick, SimpleNamespace(close=10, change_pct=0, amount=1e8, vol_ratio=None), "2026-09-21")
    assert selected[0]["kind"] == "inferred"
    assert selected[2]["value"] == 0
    assert now[0]["kind"] == "unknown"
    assert now[1]["status"] == "stale"
    assert now[-1]["value"] == 0


@pytest.mark.parametrize("snapshot", [{"close": float("nan")}, {"close": {"price": 1}}, {"__import__": 1}, {"close": True}])
def test_snapshot_rejects_non_finite_nested_or_unknown_fields(snapshot):
    with pytest.raises(ValueError): validate_snapshot(snapshot)


def test_snapshot_inspects_builtin_predicates_without_querying_sources(config, monkeypatch):
    monkeypatch.setattr("httpx.get", lambda *a, **k: pytest.fail("不能联网"))
    result = diagnose({"close": 10, "change_pct": 4, "amount": 2e8, "ma20": 9, "high_20": 10, "range_20": 20, "vol_ratio": 2.2, "close_pos": .9}, StrategyScreener(config), ["volume_breakout"])
    strategy = result["strategies"][0]
    assert strategy["matched"] is True and len(strategy["checks"]) == 3
    assert all(check["status"] == "passed" for check in strategy["checks"])
    result = diagnose({"close": 10, "change_pct": 4, "amount": 2e8}, StrategyScreener(config), ["volume_breakout"])
    assert result["strategies"][0]["matched"] is None


def test_live_and_stored_history_keep_explanation_contract(config):
    screener = StrategyScreener(config)
    result = screener.run()
    assert result.picks and result.picks[0].why_selected
    assert screener.latest()[0]["why_selected"] == result.picks[0].why_selected


def test_snapshot_explains_profile_score_rejection(config):
    from src.strategy.screener import Strategy
    from src.strategy.screening_pipeline import profile_rule
    screener = StrategyScreener(config)
    definition = {"name": "score_gate", "conditions": [], "weights": {"liquidity": 1}, "min_score": 90}
    screener.profiles = [definition]
    screener.strategies = [Strategy("score_gate", "评分门槛", "", (), {}, lambda f, p: profile_rule(f, definition))]
    strategy = diagnose({"close": 10, "change_pct": 4, "amount": 2e8}, screener)["strategies"][0]
    assert strategy["matched"] is False
    check = strategy["checks"][-1]
    assert check["status"] == "failed" and check["condition"] == "weighted_score >= 90"
    assert check["inputs"]["weighted_score"] < 90 and check["inputs"]["factor_coverage"] == 1
    strategy = diagnose({"close": 10, "change_pct": 4}, screener)["strategies"][0]
    assert strategy["matched"] is None and strategy["checks"][-1]["missing"] == ["amount"]
