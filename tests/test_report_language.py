"""AI 输出语言（WS29）：report_language 模块、各服务英文输出、API。离线运行，假 LLM 记录收到的 system_message。"""

from __future__ import annotations

import inspect
import re
from datetime import datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthStore
from src import settings_store
from src.analyzers import market_regime as regime_mod
from src.analyzers.market_regime import MarketRegime
from src.collectors import daily_history as daily_history_mod
from src.collectors import fundamentals as fundamentals_mod
from src.collectors import news_search
from src.collectors import stock_news as stock_news_mod
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import FundDaily, FundInfo, StockDaily, StockInfo
from src.services import market_phase as mp
from src.services import market_review as review_mod
from src.services import report_language as rl
from src.services.market_context import MarketFacts
from src.services.market_review import MarketReviewService
from src.services.market_review import render_markdown as review_markdown
from src.services.stock_diagnosis import StockDiagnosisService, render_markdown
from src.services.stock_search import StockSearch

CJK = re.compile(r"[一-鿿]")
DAYS = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end="2026-09-25", periods=25)]


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


class RecLLM:
    """记录 user_message / system_message 的假 LLM（chat_json、chat 都支持）。"""

    def __init__(self, reply: dict | None = None, text: str = "# Report\n\nconclusion [E1]"):
        self.reply, self.text = reply or {}, text
        self.users: list[str] = []
        self.systems: list[str] = []

    def chat_json(self, user_message: str, system_message: str = "", **kwargs) -> dict:
        self.users.append(user_message)
        self.systems.append(system_message)
        return self.reply

    def chat(self, user_message: str, system_message: str = "", **kwargs) -> str:
        self.users.append(user_message)
        self.systems.append(system_message)
        return self.text


# ---------- report_language 模块 ----------

def test_report_language_values():
    assert rl.report_language({"report": {"language": "en"}}) == "en"
    assert rl.report_language({"report": {"language": " EN "}}) == "en"
    assert rl.report_language({"report": {"language": "zh"}}) == "zh"
    for bad in ({}, None, {"report": None}, {"report": {}}, {"report": {"language": ""}},
                {"report": {"language": "fr"}}, {"report": {"language": 123}}, {"report": "en"}):
        assert rl.report_language(bad) == "zh"


def test_language_directive():
    assert rl.language_directive("zh") == "" and rl.language_directive("zh", "action: buy") == ""
    assert rl.language_directive("xx") == ""
    en = rl.language_directive("en")
    assert "English" in en and "JSON" in en
    with_enums = rl.language_directive("en", "`action` uses buy/add/hold; `confidence` uses 高/中/低")
    assert "buy/add/hold" in with_enums and "高/中/低" in with_enums
    assert with_enums.startswith(en.split(".")[0][:10]) or len(with_enums) > len(en)


def test_tr_and_display():
    assert rl.tr("zh", "中文", "English") == "中文"
    assert rl.tr("en", "中文", "English") == "English"
    assert rl.tr("fr", "中文", "English") == "中文"
    for zh, en in {"高": "High", "中": "Medium", "低": "Low", "看多": "Bullish", "看空": "Bearish",
                   "进攻": "Offensive", "均衡": "Balanced", "防守": "Defensive", "买入": "Buy", "加仓": "Add",
                   "持有": "Hold", "观望": "Watch", "减仓": "Reduce", "卖出": "Sell", "回避": "Avoid", "提醒": "Alert"}.items():
        assert rl.LABELS_EN[zh] == en
        assert rl.display("en", zh) == en and rl.display("zh", zh) == zh
    for phase in ("盘前", "盘中", "午间休市", "临近收盘", "盘后", "非交易日"):
        assert phase in rl.LABELS_EN and not CJK.search(rl.LABELS_EN[phase])
    assert rl.display("en", "未知词") == "未知词"      # 查不到原样返回
    assert rl.display("en", None) is None and rl.display("en", 5) == 5


# ---------- 个股诊断 ----------

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(fundamentals_mod, "fetch_chip_summary", lambda *a, **k: None)
    monkeypatch.setattr(fundamentals_mod.EarningsCache, "get", classmethod(lambda cls, code: None))
    monkeypatch.setattr(stock_news_mod, "get_stock_news", lambda *a, **k: {"news": [], "notices": []})
    monkeypatch.setattr(daily_history_mod, "ensure_daily_history", lambda *a, **k: 0)
    news_search.reset_state()
    yield
    news_search.reset_state()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "lang.db")
    _reset_db_engine()
    init_db(path)
    with get_db_session(path) as session:
        for i, d in enumerate(DAYS):
            close = 14 + i * 0.4
            session.add(StockDaily(code="sz002594", name="比亚迪", trade_date=d, open=close - 0.2, close=close,
                                   change_pct=2.0, volume=1000 + i, amount=5e9, turnover=3.2, circ_mv=6e11))
    yield path
    _reset_db_engine()


REPLY = {
    "score": 82, "action": "买入", "confidence": "高", "one_sentence": "Leader, buy on dips",
    "position_advice": {"no_position": "buy on pullback", "has_position": "hold"},
    "battle_plan": {"buy_price": 23.8, "stop_loss": 22.5, "target_price": 26.0, "suggested_position": "30%"},
    "catalysts": ["solid-state battery"], "risks": ["crowded trade"],
    "checklist": [{"item": "theme", "status": "pass", "note": "leader"}],
    "analysis": "Second board relay.",
    "phase_decision": {"trading_window": "first 30 min", "immediate_action": "watch open", "watch_conditions": ["volume"],
                       "next_check_time": "9:25"},
    "signal_attribution": {"technical": "40%", "news": 30, "fundamentals": 20, "market": "10",
                           "strongest_bullish": "leader", "strongest_bearish": "crowded"},
}


def _svc(db_path, reply=None, lang="en", extra=None):
    llm = RecLLM(REPLY if reply is None else reply)
    config = {"database": {"sqlite_path": db_path}, "risk": {}, "trading": {}, **(extra or {})}
    if lang:
        config["report"] = {"language": lang}
    return StockDiagnosisService(config, llm=llm), llm


def test_diagnosis_en_directive_and_language(db_path):
    svc, llm = _svc(db_path)
    result = svc.diagnose("002594", force=True)
    assert result["language"] == "en"
    assert "English" in llm.systems[0]
    # 枚举保持原样的说明：动作用英文枚举、信心用中文
    assert "buy" in llm.systems[0] and "高" in llm.systems[0]
    assert result["action"] in ("buy", "watch")      # 枚举值仍是代码用的英文枚举（测试库数据不全可能被护栏降级）
    assert result["action_label"] in ("买入", "观望") or result["action_label"] in ("Buy", "Watch")


def test_diagnosis_zh_has_no_directive_and_language_zh(db_path):
    svc, llm = _svc(db_path, lang=None)
    result = svc.diagnose("002594", force=True)
    assert result.get("language", "zh") == "zh"
    assert "English" not in llm.systems[0] and "Output language" not in llm.systems[0]
    md = render_markdown(result)
    assert "### 操作建议" in md and "### 作战计划" in md and "仅供学习研究" in md


def test_diagnosis_en_markdown_titles(db_path):
    result = _svc(db_path)[0].diagnose("002594", force=True)
    md = render_markdown(result)
    for word in ("Position advice", "Battle plan", "Phase decision", "Signal attribution", "Risks"):
        assert word in md, word
    for zh in ("操作建议", "作战计划", "阶段决策", "信号归因", "风险提示", "仅供学习研究"):
        assert zh not in md, zh
    assert "Buy" in md                      # action_label 的英文显示名
    assert "评分" not in md and "信心" not in md


def test_markdown_follows_result_language_not_current_config(db_path):
    """render_markdown 只看 result["language"]，不读全局配置。"""
    result = _svc(db_path, lang=None)[0].diagnose("002594", force=True)
    assert "### 操作建议" in render_markdown(result)
    result["language"] = "en"
    assert "### Position advice" in render_markdown(result)


def test_diagnosis_cache_is_per_language(db_path):
    zh_svc, zh_llm = _svc(db_path, lang="zh")
    zh_svc.diagnose("002594")
    assert len(zh_llm.users) == 1
    en_svc, en_llm = _svc(db_path, lang="en")
    first = en_svc.diagnose("002594")
    assert len(en_llm.users) == 1 and first["language"] == "en" and not first.get("cached")   # 不复用中文结果
    second = en_svc.diagnose("002594")
    assert len(en_llm.users) == 1 and second.get("cached") is True and second["language"] == "en"
    # 中文再诊断不会复用英文结果
    zh_svc.diagnose("002594", force=True)
    zh_again = _svc(db_path, lang="zh")[0].diagnose("002594")
    assert zh_again.get("language", "zh") == "zh"


def test_diagnosis_old_record_without_language_counts_as_zh(db_path):
    import json

    from src.database.models import StockDiagnosis

    _svc(db_path, lang="zh")[0].diagnose("002594", force=True)
    with get_db_session(db_path) as session:
        row = session.query(StockDiagnosis).first()
        data = json.loads(row.result_json)
        data.pop("language", None)
        row.result_json = json.dumps(data, ensure_ascii=False)
    en_svc, en_llm = _svc(db_path, lang="en")
    en_svc.diagnose("002594")
    assert len(en_llm.users) == 1        # 旧记录视为 zh，英文请求不复用
    zh_svc, zh_llm = _svc(db_path, lang="zh")
    zh_svc.diagnose("002594")
    assert len(zh_llm.users) in (0, 1)   # 英文结果更新，是否复用不做断言


def test_invalid_language_falls_back_to_zh(db_path):
    svc, llm = _svc(db_path, lang="fr")
    result = svc.diagnose("002594", force=True)
    assert result.get("language", "zh") == "zh" and "English" not in llm.systems[0]


def test_guardrails_en_low_score(db_path):
    result = _svc(db_path, {**REPLY, "score": 45, "action": "买入"})[0].diagnose("002594", force=True)
    assert result["action"] == "watch" and result["guardrails"]
    for g in result["guardrails"]:
        assert not CJK.search(g), g
    assert "45" in result["guardrails"][0]


def test_guardrails_en_frozen_regime(db_path, monkeypatch):
    monkeypatch.setattr(regime_mod.MarketRegimeAnalyzer, "analyze",
                        lambda self, **kw: MarketRegime(trade_date="2026-09-25", regime="冰点", position_factor=0.0))
    result = _svc(db_path)[0].diagnose("002594", force=True)
    assert result["action"] == "watch" and result["guardrails"]
    assert not any(w in result["guardrails"][0] for w in ("大盘", "降为", "观望", "不建议"))


def test_guardrails_en_data_quality(db_path):
    with get_db_session(db_path) as session:
        session.query(StockDaily).filter(StockDaily.trade_date < DAYS[-10]).delete()
    result = _svc(db_path)[0].diagnose("002594", force=True)
    assert result["action"] == "watch" and "10" in result["guardrails"][0]
    assert not any(w in result["guardrails"][0] for w in ("日线", "根", "降"))


def test_guardrails_en_phase_premarket_and_stale(db_path, monkeypatch):
    ctx = {"phase": "premarket", "label": "盘前", "now": "2026-09-28 08:30", "is_trading_day": True, "is_partial_bar": False,
           "minutes_to_open": 60, "minutes_to_close": None, "effective_daily_bar_date": "2026-09-25"}
    monkeypatch.setattr(mp, "current_phase", lambda now=None: dict(ctx))
    result = _svc(db_path, {**REPLY, "phase_decision": {**REPLY["phase_decision"], "immediate_action": "立即买入"}})[0] \
        .diagnose("002594", force=True)
    assert result["guardrails"] and not any("当前为" in g or "不支持" in g for g in result["guardrails"])
    assert not CJK.search(result["phase_decision"]["immediate_action"])   # 替换文案也是英文

    ctx2 = {"phase": "postmarket", "label": "盘后", "now": "2026-09-29 16:00", "is_trading_day": True,
            "is_partial_bar": False, "minutes_to_open": None, "minutes_to_close": None, "effective_daily_bar_date": "2026-09-29"}
    monkeypatch.setattr(mp, "current_phase", lambda now=None: dict(ctx2))
    stale = _svc(db_path)[0].diagnose("002594", force=True)
    assert stale["action"] == "watch" and stale["guardrails"]
    assert not any("降级" in g or "行情数据" in g for g in stale["guardrails"])


def test_phase_guardrails_lang_param():
    ctx = {"phase": "premarket", "label": "盘前", "effective_daily_bar_date": "2026-09-25", "is_trading_day": True}
    decision = {"immediate_action": "立即买入"}
    assert "lang" in inspect.signature(mp.phase_guardrails).parameters
    zh = mp.phase_guardrails("buy", "高", decision, ctx, "2026-09-25")
    zh_explicit = mp.phase_guardrails("buy", "高", decision, ctx, "2026-09-25", lang="zh")
    assert zh == zh_explicit                              # 默认 zh，逐字不变
    assert "当前为盘前，不支持立即买卖，已改为开盘后确认" in zh[3]
    en = mp.phase_guardrails("buy", "高", decision, ctx, "2026-09-25", lang="en")
    assert en[0] == zh[0] and en[1] == zh[1]              # action / confidence 不受语言影响
    assert en[3] and not any(CJK.search(n) for n in en[3])


def test_multi_agent_and_skill_consult_get_directive(db_path):
    extra = {"diagnosis": {"mode": "standard", "skill_consult": {"enabled": True, "max_skills": 2}}}
    svc, llm = _svc(db_path, extra=extra)
    svc.diagnose("002594", force=True)
    assert len(llm.systems) > 1                            # 分析员 + 决策员（+ 策略会诊）
    assert all("English" in s for s in llm.systems), [s[:40] for s in llm.systems if "English" not in s]
    zh_svc, zh_llm = _svc(db_path, lang="zh", extra=extra)
    zh_svc.diagnose("002594", force=True)
    assert not any("English" in s for s in zh_llm.systems)


def test_run_analysts_lang_param():
    from src.services import diagnosis_agents as agents

    text = "股票：比亚迪\n【行情】收盘 20\n【技术面】多头\n【资金流】流入\n【新闻】无\n"
    en_llm, zh_llm = RecLLM({"view": "看多", "score": 70, "confidence": "中", "key_points": []}), RecLLM({})
    agents.run_analysts(en_llm, text, "standard", lang="en")
    assert en_llm.systems and all("English" in s and "看多" in s for s in en_llm.systems)
    agents.run_analysts(zh_llm, text, "standard")
    assert zh_llm.systems and not any("English" in s for s in zh_llm.systems)


# ---------- 大盘复盘 ----------

REVIEW_REPLY = {"headline": "Shrinking volume", "trend": "down", "emotion": "weak", "main_lines": "none", "stance": "进攻",
                "position": "8 成", "focus": ["x"], "avoid": ["y"], "watch_points": ["z"]}


def _regime(regime="防守", score=38.0, trade_date="2026-09-24"):
    from types import SimpleNamespace

    return SimpleNamespace(regime=regime, score=score, trade_date=trade_date, reasons=["跌多涨少"],
                           summary=lambda: f"大盘环境：{regime}（{score:.0f}分）")


@pytest.fixture
def review_cfg(tmp_path, monkeypatch):
    path = str(tmp_path / "rv.db")
    _reset_db_engine()
    init_db(path)
    monkeypatch.setattr(review_mod, "build_market_facts", lambda cfg, overview=None: MarketFacts(overview={"up_count": 1}, regime=_regime()))
    yield lambda lang: {"database": {"sqlite_path": path}, "llm": {}, **({"report": {"language": lang}} if lang else {})}
    _reset_db_engine()


def test_review_en(review_cfg):
    llm = RecLLM(REVIEW_REPLY)
    result = MarketReviewService(review_cfg("en"), llm=llm).generate()
    assert result["language"] == "en" and "English" in llm.systems[0]
    assert "进攻" in llm.systems[0] and "防守" in llm.systems[0]          # stance 枚举保持中文的说明
    assert result["stance"] == "防守"                                       # 枚举仍是中文，且被护栏下调
    assert result["guardrails"] and not any(w in result["guardrails"][0] for w in ("量化大盘环境", "下调", "姿态"))
    md = review_markdown(result)
    assert not any(w in md for w in ("次日姿态", "次日关注", "建议仓位"))
    assert "Defensive" in md or "defensive" in md.lower()


def test_review_zh_unchanged_and_cache_per_language(review_cfg):
    llm = RecLLM(REVIEW_REPLY)
    zh = MarketReviewService(review_cfg(None), llm=llm).generate()
    assert "English" not in llm.systems[0] and zh.get("language", "zh") == "zh"
    assert "次日姿态：**防守**" in zh["markdown"]
    assert zh["guardrails"] == ["量化大盘环境为「防守」（38分），姿态由「进攻」下调为「防守」"]
    # 中文复盘已存在，英文请求不复用
    en_llm = RecLLM(REVIEW_REPLY)
    en = MarketReviewService(review_cfg("en"), llm=en_llm).generate()
    assert len(en_llm.users) == 1 and en["language"] == "en"
    # 英文已有，再请求英文复用
    again = MarketReviewService(review_cfg("en"), llm=en_llm).generate()
    assert len(en_llm.users) == 1 and again["created_at"] == en["created_at"]


# ---------- 问股 ----------

def test_chat_directive_sync_and_stream():
    from src.services.stock_chat import StockChatSession

    class Tools:
        def call(self, name, args):
            return "x"

    llm = RecLLM({"answer": "Hold."})
    chat = StockChatSession({"report": {"language": "en"}}, llm=llm, tools=Tools())
    turn = chat.ask("茅台怎么样")
    assert turn.answer == "Hold." and "English" in llm.systems[0]
    assert "tool_calls" in llm.systems[0] and "answer" in llm.systems[0]   # JSON 协议键不变

    zh_llm = RecLLM({"answer": "持有"})
    StockChatSession({}, llm=zh_llm, tools=Tools()).ask("茅台怎么样")
    assert "English" not in zh_llm.systems[0]

    class StreamLLM:
        def __init__(self):
            self.systems = []

        def chat_stream(self, user_message, system_message="", **kw):
            self.systems.append(system_message)
            yield '{"answer": "Hold it."}'

    sllm = StreamLLM()
    events = list(StockChatSession({"report": {"language": "en"}}, llm=sllm, tools=Tools()).ask_stream("茅台怎么样"))
    assert events[-1]["type"] == "done" and sllm.systems and "English" in sllm.systems[0]


# ---------- 深度研究 ----------

def test_research_report_directive(tmp_path, monkeypatch):
    from src.services.research import ResearchService

    path = str(tmp_path / "r.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    monkeypatch.setattr(news_search, "is_enabled", lambda c: False)

    class Tools:
        def call(self, name, args=None):
            return f"{name} ok"

    plan = {"questions": ["q1"], "stocks": [], "keywords": ["k"]}
    en_llm, zh_llm = RecLLM(plan), RecLLM(plan)
    cfg = {"database": {"sqlite_path": path}, "llm": {"cache_path": str(tmp_path / "c.sqlite3")}}
    en = ResearchService({**cfg, "report": {"language": "en"}}, llm=en_llm, tools=Tools()).run("solid state battery")
    ResearchService(cfg, llm=zh_llm, tools=Tools()).run("固态电池")
    assert en["markdown"].startswith("# Report")
    assert any("English" in s for s in en_llm.systems)       # 至少报告生成这一步带英文指令
    assert not any("English" in s for s in zh_llm.systems)
    assert any("E1" in s or "E1" in u for s, u in zip(en_llm.systems, en_llm.users))   # 证据编号格式仍在提示里
    _reset_db_engine()
    StockSearch.reset()


# ---------- 基金诊断 ----------

def test_fund_diagnosis_directive(tmp_path, monkeypatch):
    from src.collectors import fund_data
    from src.services.fund_diagnosis import FundDiagnosisService

    path = str(tmp_path / "f.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end="2026-09-29", periods=70)]
    with get_db_session(path) as session:
        session.add(StockInfo(code="600519", name="贵州茅台"))
        session.add(FundInfo(code="510300", name="沪深300ETF", kind="etf", exchange="sh", updated_at=datetime.now()))
        for i, d in enumerate(days):
            session.add(FundDaily(code="510300", name="沪深300ETF", trade_date=d, open=4 + i * 0.01, high=4.1 + i * 0.01,
                                  low=3.9 + i * 0.01, close=4.02 + i * 0.01, volume=1e8, amount=5e8, change_pct=0.3))
    monkeypatch.setattr(fund_data, "ensure_fund_daily", lambda *a, **k: 0)
    monkeypatch.setattr(fund_data, "refresh_recent_fund_daily", lambda *a, **k: 0)
    monkeypatch.setattr(regime_mod.MarketRegimeAnalyzer, "analyze",
                        lambda self, **kw: MarketRegime(trade_date="2026-09-29", regime="均衡", position_factor=1.0))
    reply = {"score": 78, "action": "买入", "confidence": "中", "one_sentence": "uptrend",
             "position_advice": {"no_position": "a", "has_position": "b"},
             "battle_plan": {"buy_price": None, "stop_loss": None, "target_price": None, "suggested_position": "3成"},
             "catalysts": [], "risks": ["r"], "checklist": [], "analysis": "ok"}
    cfg = {"database": {"sqlite_path": path}, "risk": {}, "trading": {}}
    en_llm = RecLLM(reply)
    result = FundDiagnosisService({**cfg, "report": {"language": "en"}}, llm=en_llm).diagnose("510300", force=True)
    assert not result.get("error"), result
    assert result["language"] == "en" and "English" in en_llm.systems[0]
    assert "Position advice" in render_markdown(result)
    zh_llm = RecLLM(reply)
    zh = FundDiagnosisService(cfg, llm=zh_llm).diagnose("510300", force=True)
    assert "English" not in zh_llm.systems[0] and zh.get("language", "zh") == "zh"
    _reset_db_engine()
    StockSearch.reset()


# ---------- 自选股决策仪表盘 ----------

def _items():
    return [{"code": "002594", "name": "BYD", "kind": "stock", "action": "buy", "action_label": "Buy", "score": 82,
             "one_sentence": "leader", "change": "First diagnosis", "battle_plan": {"buy_price": 23.8, "stop_loss": 22.5, "target_price": 26.0},
             "catalysts": ["battery"], "risks": ["crowded"], "guardrails": ["note"]},
            {"code": "600000", "name": "SPDB", "kind": "stock", "action": "avoid", "action_label": "Avoid", "score": 30,
             "one_sentence": "weak", "change": "-1%", "battle_plan": {}, "catalysts": [], "risks": [], "guardrails": []}]


def test_dashboard_en_and_zh_default():
    from src.services.watchlist_report import render_dashboard

    zh = render_dashboard("2026-09-25", _items(), [{"code": "1", "name": "X", "error": "boom"}])
    assert "自选股决策仪表盘" in zh and "个股要点" in zh and "结论摘要" in zh and "仅供学习研究" in zh
    stamp = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}，")
    explicit = render_dashboard("2026-09-25", _items(), [{"code": "1", "name": "X", "error": "boom"}], lang="zh")
    assert stamp.sub("T，", explicit) == stamp.sub("T，", zh)
    en = render_dashboard("2026-09-25", _items(), [{"code": "1", "name": "X", "error": "boom"}], lang="en")
    assert not CJK.search(en), CJK.findall(en)
    assert "2026-09-25" in en and "BYD(002594)" in en and "82" in en
    assert "dashboard" in en.lower()


def test_change_text_en():
    from src.services import watchlist_report as wr

    if "lang" not in inspect.signature(wr.change_text).parameters:
        pytest.skip("change_text 未提供 lang 参数（由 _diagnose_one 英文化也可）")
    assert wr.change_text({"action": "buy"}, None, lang="en") != "首次诊断"
    assert not CJK.search(wr.change_text({"action": "buy", "action_label": "Buy", "score": 80},
                                         {"action": "hold", "action_label": "Hold", "score": 60, "created_at": "2026-09-24 16:00:00"}, lang="en"))


# ---------- API ----------

def test_report_settings_api_roundtrip(tmp_path, monkeypatch):
    """保存后立即读取得到新值（reload_config 读真实临时 settings.yaml）。"""
    from src import config_loader

    path = str(tmp_path / "api.db")
    _reset_db_engine()
    init_db(path)
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(f"database:\n  sqlite_path: {path!r}\n", encoding="utf-8")
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", settings_file)
    config_loader.reload_config()
    def reload_from_tmp():
        config_loader._config_cache.pop(str(settings_file), None)      # 清掉临时文件的缓存
        return config_loader.load_config(str(settings_file))

    monkeypatch.setattr(config_loader, "reload_config", reload_from_tmp)
    config = {"database": {"sqlite_path": path}, "web": {}, "risk": {}, "report": {"language": "zh"}}
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html>app</html>", encoding="utf-8")
    app = create_app(config, start_scheduler=False, static_dir=static, auth=AuthStore(tmp_path / "auth.json"))
    with TestClient(app) as client:
        assert client.get("/api/v1/settings/report").json() == {"language": "zh"}
        resp = client.put("/api/v1/settings/report", json={"language": "en"})
        assert resp.status_code == 200 and resp.json()["language"] == "en"
        assert "en" in settings_file.read_text(encoding="utf-8")
        assert client.get("/api/v1/settings/report").json()["language"] == "en"
        for bad in ({"language": "fr"}, {"language": ""}, {}, {"language": 5}):
            assert client.put("/api/v1/settings/report", json=bad).status_code == 422, bad
        assert client.get("/api/v1/settings/report").json()["language"] == "en"   # 非法值不改配置
        assert client.put("/api/v1/settings/report", json={"language": "zh"}).json()["language"] == "zh"
    _reset_db_engine()
    config_loader.reload_config()
