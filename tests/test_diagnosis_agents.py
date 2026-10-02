"""多智能体诊断、历史校准、估值数据。"""

from __future__ import annotations

import pytest

from src.collectors import source_chain as sc
from src.collectors.source_chain import source_health
from src.database.db import get_db_session
from src.database.models import StockDaily
from src.services import diagnosis_outcome as outcome_mod
from src.services.diagnosis_agents import DECISION_ADDENDUM, disagreement, split_sections
from src.services.stock_diagnosis import StockDiagnosisService, render_markdown, valuation_text
from tests.test_stock_diagnosis import GOOD_REPLY, db_path, offline_fundamentals  # noqa: F401  复用诊断测试的行情和离线桩
from tests.test_validation_loop import diag_db  # noqa: F401  复用事后验证测试的诊断记录

ANALYST_REPLIES = {
    "技术面分析员": {"view": "看多", "score": 80, "confidence": "高", "key_points": ["二板未炸板"], "risks": ["高位"]},
    "情报分析员": {"view": "看空", "score": 40, "confidence": "中", "key_points": ["异常波动公告"], "risks": []},
    "风险分析员": RuntimeError("timeout"),
}


class RoutingLLM:
    """按系统提示词区分分析员和决策员。"""

    def __init__(self, decision: dict):
        self.decision, self.calls = decision, []

    def chat_json(self, user_message, system_message="", **kwargs):
        role = next((r for r in ANALYST_REPLIES if r in system_message), "决策员")
        self.calls.append((role, user_message, system_message))
        reply = ANALYST_REPLIES.get(role, self.decision)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    outcome_mod.reset_calibration_cache()
    sc._breakers.clear()
    source_health.reset()
    yield
    outcome_mod.reset_calibration_cache()


def _service(path: str, llm, **diagnosis) -> StockDiagnosisService:
    return StockDiagnosisService({"database": {"sqlite_path": path}, "risk": {}, "trading": {}, "diagnosis": diagnosis}, llm=llm)


def test_split_sections_and_disagreement():
    sections = split_sections("股票：比亚迪(002594) 主板\n【行情】收盘 23.6\n【近 30 天公告】无\n普通行")
    assert sections == {"股票": "股票：比亚迪(002594) 主板", "行情": "【行情】收盘 23.6", "近 30 天公告": "【近 30 天公告】无"}
    tech = {"label": "技术面分析员", "view": "看多", "score": 80}
    intel = {"label": "情报分析员", "view": "看空", "score": 40}
    assert disagreement([tech, intel]) == "技术面看多、情报看空；评分相差 40 分"
    assert disagreement([tech, {**intel, "view": "中性", "score": 70}]) == ""
    assert disagreement([tech, {"label": "情报分析员", "error": "x"}]) == ""


def test_standard_mode(db_path):  # noqa: F811
    llm = RoutingLLM(GOOD_REPLY)
    result = _service(db_path, llm, mode="standard").diagnose("002594", force=True)

    by_role = {role: (msg, sys) for role, msg, sys in llm.calls}
    assert set(by_role) == {"技术面分析员", "情报分析员", "决策员"}
    tech_msg, intel_msg = by_role["技术面分析员"][0], by_role["情报分析员"][0]
    assert "【技术面】" in tech_msg and "【资金流】" in tech_msg and "【相关资讯】" not in tech_msg   # 分析员只看自己的数据
    assert "【近 30 天公告】" in intel_msg and "【技术面】" not in intel_msg
    decision_msg, decision_sys = by_role["决策员"]
    assert decision_sys.endswith(DECISION_ADDENDUM)
    assert "- 技术面分析员：看多 80分（信心高）；要点：二板未炸板；风险：高位" in decision_msg
    assert "【分歧】技术面看多、情报看空；评分相差 40 分" in decision_msg

    assert [a["view"] for a in result["agents"]] == ["看多", "看空"]
    assert result["disagreement"] == "技术面看多、情报看空；评分相差 40 分"
    assert result["confidence"] == "中" and any("分析员观点分歧" in g for g in result["guardrails"])
    md = render_markdown(result)
    assert "### 分析员观点" in md and "**分歧**：技术面看多、情报看空" in md


def test_single_and_full_modes(db_path):  # noqa: F811
    llm = RoutingLLM(GOOD_REPLY)
    result = _service(db_path, llm).diagnose("002594", force=True)   # 未配置时一次调用
    assert [c[0] for c in llm.calls] == ["决策员"] and result["agents"] == [] and "分析员观点" not in render_markdown(result)

    llm = RoutingLLM(GOOD_REPLY)
    result = _service(db_path, llm, mode="full").diagnose("002594", force=True)
    assert sorted(c[0] for c in llm.calls) == ["决策员", "情报分析员", "技术面分析员", "风险分析员"]
    assert result["agents"][2]["error"] and "- 风险分析员：未能给出观点" in llm.calls[-1][1]   # 单个分析员失败不影响诊断


def test_calibration_lowers_bullish_confidence(db_path, monkeypatch):  # noqa: F811
    stats = {"看多": {"n": 20, "accuracy": 30.0}, "看空": {"n": 0, "accuracy": None},
             "details": [{"code": "002594", "trade_date": "2026-09-20", "action_label": "买入", "r3": -2.0}]}
    monkeypatch.setattr(outcome_mod, "calibration_stats", lambda config, now=None: stats)
    llm = RoutingLLM({**GOOD_REPLY, "confidence": "中"})
    result = _service(db_path, llm).diagnose("002594", force=True)
    assert "【历史表现】近 90 天 AI 诊断：看多诊断 20 次，3 日方向准确率 30.0%；本股最近：09-20 买入→3日-2.0%" in llm.calls[-1][1]
    assert result["confidence"] == "低" and "近 90 天看多诊断 3 日准确率仅 30.0%（20 次），信心下调为低" in result["guardrails"]
    assert "**历史表现**：近 90 天 AI 诊断" in render_markdown(result)

    llm = RoutingLLM(GOOD_REPLY)
    result = _service(db_path, llm, calibration=False).diagnose("002594", force=True)
    assert "【历史表现】" not in llm.calls[-1][1] and result["calibration"] == ""


def test_calibration_stats_from_outcomes(diag_db):  # noqa: F811
    stats = outcome_mod.calibration_stats(diag_db)
    assert stats["看多"] == {"n": 1, "accuracy": 100.0} and stats["看空"] == {"n": 1, "accuracy": 100.0}
    assert outcome_mod.calibration_text(stats, "600001") == (
        "【历史表现】近 90 天 AI 诊断：看多诊断 1 次，3 日方向准确率 100.0%；看空诊断 1 次，3 日方向准确率 100.0%；"
        "本股最近：08-10 买入→3日+8.0%")
    assert outcome_mod.calibration_stats(diag_db) is stats   # 30 分钟内用缓存
    assert outcome_mod.calibration_text({"看多": {"n": 0}, "看空": {"n": 0}, "details": []}, "600001") == ""


# ---------- 估值 ----------

def test_valuation_text_and_context(db_path):  # noqa: F811
    assert valuation_text(19.09, 6.19) == "市盈率 19.1，市净率 6.19"
    assert valuation_text(-35.2, 1.5) == "亏损（市盈率为负），市净率 1.50"
    assert valuation_text(None, None) == ""
    with get_db_session(db_path) as session:
        session.query(StockDaily).filter(StockDaily.trade_date == "2026-09-25").update({"pe": 25.3, "pb": 4.1})
    context = StockDiagnosisService({"database": {"sqlite_path": db_path}}, llm=RoutingLLM({})).build_context("002594")
    assert "流通市值 6000.0 亿，市盈率 25.3，市净率 4.10" in context["text"]


def test_tencent_quotes_include_pe_pb(tmp_path, monkeypatch):
    import httpx

    from src.collectors.stock_data import StockDataCollector
    from src.database import db as db_module
    from src.database.db import init_db

    path = str(tmp_path / "q.db")
    db_module._engine = db_module._SessionFactory = None
    init_db(path)
    fields = ["1", "贵州茅台", "600519", "1243.88", "1237.00", "1236.00"] + [""] * 26
    fields += ["0.56", "1244.01", "1228.10", "", "28218", "348872", "0.23", "19.09", "", "", "", "",
               "15549.52", "15549.52", "6.19", "", ""]
    fields[30] = "20260928150000"
    text = 'v_sh600519="' + "~".join(fields) + '";'

    class _Resp:
        status_code = 200

        def __init__(self, t):
            self.text = t

    monkeypatch.setattr(httpx, "get", lambda url, **kw: _Resp(text))
    collector = StockDataCollector({"data_sources": {"realtime": ["tencent"]}})
    monkeypatch.setattr(collector, "_get_all_stock_codes", lambda: ["sh600519"])
    collector._collect_realtime_quotes("2026-09-28", path)
    with get_db_session(path) as session:
        row = session.query(StockDaily.code, StockDaily.close, StockDaily.pe, StockDaily.pb, StockDaily.amount).one()
    assert tuple(row) == ("600519", 1243.88, 19.09, 6.19, 3488720000.0)
    db_module._engine.dispose()
    db_module._engine = db_module._SessionFactory = None
