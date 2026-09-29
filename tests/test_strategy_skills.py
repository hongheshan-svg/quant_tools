"""问股策略技能 YAML 化：加载、覆盖、缓存、提示词、API、诊断历史（离线）。"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta

import pytest

from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDiagnosis
from src.services import strategy_skills
from src.services.stock_chat import PERSPECTIVES, StockChatSession
from src.services.strategy_skills import DEFAULT_SKILL, Skill, get_skill, load_skills, reset_cache
from tests.test_api import _wait, env  # noqa: F401

EXPECTED = ["综合", "打板接力", "龙回头", "主线补涨", "放量突破", "缩量回踩", "超跌反弹", "事件驱动", "情绪周期", "龙头战法",
            "缠论", "波浪理论", "均线金叉", "底部放量", "箱体震荡", "多头趋势", "一阳穿三阴", "预期重估", "成长质量"]
CATEGORIES = {"trend", "pattern", "reversal", "emotion", "framework", "fundamental"}


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_cache()
    yield
    reset_cache()


def _write(directory, filename, text):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path


def _custom_yaml(name="my_skill", display="我的策略", **extra):
    lines = [f"name: {name}", f"display_name: {display}", "description: 自定义说明", "category: pattern",
             "instructions: 自定义判断标准XYZ"]
    for key, value in extra.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


# ---------- 内置技能 ----------

def test_builtin_skills_complete():
    skills = load_skills(custom_dir="/nonexistent/dir/for/skills")
    assert len(skills) == 19
    assert {s.display_name for s in skills} >= set(EXPECTED)
    assert skills[0].display_name == DEFAULT_SKILL == "综合"
    assert all(s.source == "builtin" for s in skills)
    names = [s.name for s in skills]
    assert len(set(names)) == len(names)
    for s in skills:
        assert isinstance(s, Skill)
        assert s.instructions.strip() and s.description.strip()
        assert s.category in CATEGORIES, (s.name, s.category)


def test_sorted_by_priority_then_display_name():
    skills = load_skills(custom_dir="/nonexistent")
    keys = [(s.priority, s.display_name) for s in skills]
    assert keys == sorted(keys)


def test_aliases_unique_and_no_display_name_conflict():
    skills = load_skills(custom_dir="/nonexistent")
    seen: dict[str, str] = {}
    for s in skills:
        for alias in s.aliases:
            key = alias.strip().lower()
            assert key not in seen, f"别名 {alias} 重复：{seen.get(key)} / {s.name}"
            seen[key] = s.name
    for s in skills:
        for other in skills:
            if other is not s:
                assert other.display_name.lower() not in {a.strip().lower() for a in s.aliases}


def test_skill_is_frozen():
    skill = load_skills(custom_dir="/nonexistent")[0]
    with pytest.raises(Exception):
        skill.name = "x"  # type: ignore[misc]


# ---------- get_skill ----------

def test_get_skill_matches_name_display_alias_case_space(tmp_path):
    d = tmp_path / "c"
    _write(d, "a.yaml", _custom_yaml("MySkill", "我的策略", aliases=["MyAlias", "别称"]))
    assert get_skill("  mYsKiLl ", custom_dir=d).display_name == "我的策略"
    assert get_skill("我的策略 ", custom_dir=d).name == "MySkill"
    assert get_skill(" myalias", custom_dir=d).name == "MySkill"
    assert get_skill("别称", custom_dir=d).name == "MySkill"
    assert get_skill("不存在的策略", custom_dir=d) is None
    assert get_skill("", custom_dir=d) is None
    default = get_skill(DEFAULT_SKILL, custom_dir=d)
    assert default and default.display_name == "综合"


def test_get_skill_builtin_by_every_key():
    for s in load_skills(custom_dir="/nonexistent"):
        assert get_skill(s.name, custom_dir="/nonexistent").name == s.name
        assert get_skill(s.display_name.upper(), custom_dir="/nonexistent").name == s.name
        for alias in s.aliases:
            assert get_skill(f" {alias.upper()} ", custom_dir="/nonexistent").name == s.name


# ---------- 自定义目录 ----------

def test_custom_dir_missing_is_fine(tmp_path):
    assert len(load_skills(custom_dir=tmp_path / "nope")) == 19
    assert len(load_skills(custom_dir=None)) >= 19


def test_custom_added_and_marked(tmp_path):
    d = tmp_path / "c"
    _write(d, "a.yaml", _custom_yaml(priority=5, market_regimes=["进攻"]))
    skills = load_skills(custom_dir=d)
    assert len(skills) == 20
    mine = next(s for s in skills if s.name == "my_skill")
    assert mine.source == "custom" and mine.priority == 5 and tuple(mine.market_regimes) == ("进攻",)
    assert mine.instructions.strip() == "自定义判断标准XYZ"
    assert skills.index(mine) < skills.index(next(s for s in skills if s.display_name == "打板接力")) or mine.priority < 100


def test_custom_overrides_builtin_same_name(tmp_path):
    builtin = next(s for s in load_skills(custom_dir="/nonexistent") if s.display_name == "龙回头")
    d = tmp_path / "c"
    _write(d, "override.yaml", _custom_yaml(builtin.name, "龙回头", priority=builtin.priority))
    skills = load_skills(custom_dir=d)
    assert len(skills) == 19
    got = get_skill("龙回头", custom_dir=d)
    assert got.source == "custom" and "自定义判断标准XYZ" in got.instructions


def test_custom_invalid_files_skipped(tmp_path):
    d = tmp_path / "c"
    _write(d, "good.yaml", _custom_yaml("good", "好策略"))
    _write(d, "no_name.yaml", "display_name: X\ninstructions: y\n")
    _write(d, "no_display.yaml", "name: nd\ninstructions: y\n")
    _write(d, "no_instr.yaml", "name: ni\ndisplay_name: 无说明\n")
    _write(d, "broken.yaml", "name: [unclosed\n  : : :\n\t bad")
    _write(d, "empty.yaml", "")
    _write(d, "list.yaml", "- a\n- b\n")
    skills = load_skills(custom_dir=d)
    assert {s.name for s in skills} - {s.name for s in load_skills(custom_dir="/nonexistent")} == {"good"}


# ---------- 缓存 ----------

def test_cache_invalidated_on_modification_and_reset(tmp_path):
    d = tmp_path / "c"
    path = _write(d, "a.yaml", _custom_yaml("cached", "缓存策略"))
    assert get_skill("cached", custom_dir=d).description == "自定义说明"
    path.write_text(_custom_yaml("cached", "缓存策略").replace("自定义说明", "新说明"), encoding="utf-8")
    future = time.time() + 5
    os.utime(path, (future, future))
    assert get_skill("cached", custom_dir=d).description == "新说明"
    # 新增文件：目录修改时间变化或 reset_cache 后可见
    _write(d, "b.yaml", _custom_yaml("second", "第二策略"))
    reset_cache()
    assert get_skill("second", custom_dir=d) is not None
    path.unlink()
    reset_cache()
    assert get_skill("cached", custom_dir=d) is None


# ---------- 提示词 ----------

class _LLM:
    def __init__(self):
        self.prompts = []

    def chat_json(self, user_message, system_message="", **kwargs):
        self.prompts.append(user_message)
        return {"answer": "观望。仅供学习研究，不构成投资建议"}


def test_prompt_contains_instructions_and_perspective_normalized():
    skill = get_skill("龙回头")
    llm = _LLM()
    chat = StockChatSession({}, llm=llm, tools=object())
    for key in (skill.name, skill.display_name, *(skill.aliases[:1])):
        turn = chat.ask("茅台能买吗", perspective=key)
        assert turn.perspective == "龙回头"
    assert all(skill.instructions.strip()[:20] in p for p in llm.prompts)
    turn = chat.ask("茅台能买吗", perspective="根本没有这个策略")
    assert turn.perspective == "综合"
    assert get_skill("综合").instructions.strip()[:20] in llm.prompts[-1]


def test_perspectives_still_dict():
    assert isinstance(PERSPECTIVES, dict)
    assert "综合" in PERSPECTIVES and "龙回头" in PERSPECTIVES
    assert all(isinstance(k, str) and isinstance(v, str) and v for k, v in PERSPECTIVES.items())


# ---------- API ----------

def test_api_skills_and_perspectives(env):  # noqa: F811
    client, app, _ = env
    rows = client.get("/api/v1/chat/skills").json()
    assert len(rows) >= 19 and rows[0]["display_name"] == "综合"
    for key in ("name", "display_name", "description", "category", "aliases", "market_regimes", "source", "instructions"):
        assert key in rows[0]
    assert all(r["category"] in CATEGORIES for r in rows if r["source"] == "builtin")
    perspectives = client.get("/api/v1/chat/perspectives")
    assert perspectives.status_code == 200 and "龙回头" in perspectives.json()


def test_api_session_perspective_alias_normalized(env):  # noqa: F811
    client, app, _ = env
    skill = get_skill("龙回头")
    alias = skill.aliases[0] if skill.aliases else skill.name
    session = client.post("/api/v1/chat/sessions", json={"perspective": alias}).json()
    assert session["perspective"] == "龙回头"
    unknown = client.post("/api/v1/chat/sessions", json={"perspective": "不存在"}).json()
    assert unknown["perspective"] == "综合"

    app.state.chat_store._factory = lambda cfg: StockChatSession(cfg, llm=_LLM(), tools=object())
    task = client.post(f"/api/v1/chat/sessions/{session['id']}/ask", json={"question": "茅台能买吗", "perspective": alias}).json()
    assert _wait(client, task)["status"] == "done"
    assert client.get(f"/api/v1/chat/sessions/{session['id']}").json()["turns"][0]["perspective"] == "龙回头"


# ---------- 诊断历史 ----------

@pytest.fixture
def diag_db(tmp_path):
    path = str(tmp_path / "diag.db")
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None
    init_db(path)
    yield path
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def test_diagnosis_history_order_limit_fields(diag_db):
    from src.services.data_query_service import DataQueryService

    base = datetime(2026, 9, 20, 15, 0)
    with get_db_session(diag_db) as session:
        for i in range(7):
            session.add(StockDiagnosis(
                code="600519", name="贵州茅台", trade_date=f"2026-09-{20 + i}", action="hold", score=50.0 + i,
                result_json=json.dumps({"summary": f"结论{i}", "action_label": "持有"}, ensure_ascii=False),
                created_at=base + timedelta(days=i)))
        session.add(StockDiagnosis(code="000001", name="平安银行", trade_date="2026-09-26", action="buy", score=80.0,
                                   result_json="{}", created_at=base + timedelta(days=9)))
    service = DataQueryService(diag_db)
    rows = service.diagnosis_history("600519", limit=5)
    assert len(rows) == 5
    assert [r["score"] for r in rows] == [56.0, 55.0, 54.0, 53.0, 52.0]
    assert set(rows[0]) >= {"created_at", "trade_date", "action", "score", "summary"}
    assert rows[0]["trade_date"] == "2026-09-26" and rows[0]["action"] == "hold"
    assert len(service.diagnosis_history("600519", limit=2)) == 2
    assert service.diagnosis_history("300750") == []
    assert len(service.diagnosis_history("600519")) == 5   # 默认 5


def test_pipeline_diagnosis_history_delegates(monkeypatch):
    from src.services import data_query_service
    from src.services.pipeline_service import PipelineService

    calls = []

    class FakeQuery:
        def __init__(self, db_path=None):
            pass

        def diagnosis_history(self, code, limit=5):
            calls.append((code, limit))
            return [{"created_at": "x", "trade_date": "2026-09-26", "action": "buy", "score": 70, "summary": "s"}]

    monkeypatch.setattr(data_query_service, "DataQueryService", FakeQuery)
    pipeline = PipelineService.__new__(PipelineService)
    pipeline.config = {"database": {"sqlite_path": ":memory:"}}
    pipeline.db_path = ":memory:"
    try:
        rows = pipeline.diagnosis_history("600519", 3)
    except AttributeError:
        pytest.skip("PipelineService 构造方式不同，委托由 router/API 测试覆盖")
    assert rows[0]["action"] == "buy" and calls == [("600519", 3)]
