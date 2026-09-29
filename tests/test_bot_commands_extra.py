"""机器人新命令：策略、批量、历史（假 pipeline / 假 chat，离线）。"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from src.bot.models import BotMessage
from src.bot.router import HELP_TEXT, CommandRouter
from src.services import strategy_skills, watchlist as watchlist_module

STOCKS = {
    "茅台": ("600519", "贵州茅台"), "600519": ("600519", "贵州茅台"),
    "宁德时代": ("300750", "宁德时代"), "宁德": ("300750", "宁德时代"),
    "比亚迪": ("002594", "比亚迪"), "平安": ("000001", "平安银行"),
    "招商": ("001872", "招商轮船"), "中远": ("601919", "中远海控"), "万科": ("000002", "万科A"),
}


class Pipe:
    db_path = ":memory:"

    def __init__(self, fail=()):
        self.diagnosed, self.fail, self.history_calls = [], set(fail), []
        self.history_rows = {}

    def diagnose_stock(self, code, force=False):
        self.diagnosed.append(code)
        name = next(n for c, n in STOCKS.values() if c == code)
        if code in self.fail:
            return {"error": "数据不足BOOM"}
        return {"code": code, "name": name, "action": "hold", "action_label": "持有", "score": 61,
                "summary": f"{name}结论"}

    def diagnosis_history(self, code, limit=5):
        self.history_calls.append((code, limit))
        return self.history_rows.get(code, [])


class Chat:
    instances: list = []

    def __init__(self):
        self.calls = []
        Chat.instances.append(self)

    def ask(self, question, perspective="综合", progress=None):
        self.calls.append((question, perspective))
        return SimpleNamespace(answer=f"答:{question}|{perspective}", error="")


def msg(text, user="u1"):
    return BotMessage(platform="dingtalk", chat_id="c1", user_id=user, user_name="张三", text=text)


@pytest.fixture
def custom_dir(tmp_path, monkeypatch):
    d = tmp_path / "config" / "strategies"
    d.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(strategy_skills, "CUSTOM_DIR", d)
    strategy_skills.reset_cache()
    yield d
    strategy_skills.reset_cache()


@pytest.fixture
def setup(monkeypatch, custom_dir):
    monkeypatch.setattr(watchlist_module.WatchlistService, "resolve", lambda self, text: STOCKS.get(text))
    Chat.instances = []
    pipe = Pipe()
    router = CommandRouter({"database": {"sqlite_path": ":memory:"}}, pipeline=pipe, chat_factory=Chat)
    return router, pipe


def _skill_yaml(name, display, aliases=()):
    al = "".join(f"\n  - {a}" for a in aliases)
    return (f"name: {name}\ndisplay_name: {display}\ndescription: 说明{display}\ncategory: trend\n"
            f"aliases:{al if al else ' []'}\ninstructions: 标准{display}\n")


# ---------- 策略 ----------

def test_help_mentions_new_commands():
    for word in ("策略", "批量", "历史"):
        assert word in HELP_TEXT


def test_strategy_list(setup):
    router, _ = setup
    reply = router.handle(msg("策略"))
    skills = strategy_skills.load_skills()
    for s in skills:
        assert s.display_name in reply
    assert not Chat.instances or all(not c.calls for c in Chat.instances)


def test_strategy_with_display_name_and_question(setup):
    router, _ = setup
    reply = router.handle(msg("策略龙回头 宁德时代能买吗"))
    chat = Chat.instances[0]
    assert len(chat.calls) == 1
    question, perspective = chat.calls[0]
    assert perspective == "龙回头" and "宁德时代能买吗" in question
    assert reply.startswith("答:")


def test_strategy_with_slash_space_and_name(setup):
    router, _ = setup
    skill = strategy_skills.get_skill("龙回头")
    router.handle(msg(f"/策略 {skill.name} 宁德时代"))
    question, perspective = Chat.instances[0].calls[0]
    assert perspective == "龙回头" and "宁德时代" in question


def test_strategy_alias_case_insensitive(setup):
    router, _ = setup
    skill = strategy_skills.get_skill("龙回头")
    for alias in skill.aliases[:1] or (skill.name,):
        router.handle(msg(f"策略 {alias.upper()} 宁德时代", user="u2"))
    assert Chat.instances[-1].calls[0][1] == "龙回头"


def test_strategy_only_name_returns_description(setup):
    router, _ = setup
    skill = strategy_skills.get_skill("龙回头")
    reply = router.handle(msg("策略龙回头"))
    assert skill.description[:10] in reply
    assert not any(c.calls for c in Chat.instances)


def test_strategy_non_skill_goes_to_chat_whole_sentence(setup):
    router, _ = setup
    reply = router.handle(msg("策略选股今天选了什么"))
    question, perspective = Chat.instances[0].calls[0]
    assert question == "策略选股今天选了什么" and perspective == "综合"
    assert "策略选股今天选了什么" in reply


def test_strategy_longest_alias_match(setup, custom_dir):
    (custom_dir / "a.yaml").write_text(_skill_yaml("short_one", "趋势甲", ["趋势"]), encoding="utf-8")
    (custom_dir / "b.yaml").write_text(_skill_yaml("long_one", "趋势乙", ["趋势加速"]), encoding="utf-8")
    strategy_skills.reset_cache()
    router, _ = setup
    router.handle(msg("策略趋势加速 宁德时代"))
    question, perspective = Chat.instances[0].calls[0]
    assert perspective == "趋势乙" and question.strip().startswith("宁德时代")
    router.handle(msg("策略趋势 宁德时代", user="u9"))
    assert Chat.instances[1].calls[0][1] == "趋势甲"


def test_strategy_custom_skill_usable(setup, custom_dir):
    (custom_dir / "c.yaml").write_text(_skill_yaml("mine", "我的战法", ["wode"]), encoding="utf-8")
    strategy_skills.reset_cache()
    router, _ = setup
    assert "我的战法" in router.handle(msg("策略"))
    router.handle(msg("策略 wode 茅台"))
    assert Chat.instances[0].calls[0][1] == "我的战法"


# ---------- 批量 ----------

def test_batch_diagnoses_each_and_summarizes(setup):
    router, pipe = setup
    reply = router.handle(msg("批量 茅台 宁德时代"))
    assert pipe.diagnosed == ["600519", "300750"]
    assert "贵州茅台" in reply and "宁德时代" in reply


def test_batch_separators_and_alias_command(setup):
    router, pipe = setup
    reply = router.handle(msg("批量诊断 茅台,宁德时代、比亚迪"))
    assert pipe.diagnosed == ["600519", "300750", "002594"]
    assert "比亚迪" in reply
    pipe.diagnosed.clear()
    router.handle(msg("/批量 茅台，平安"))
    assert pipe.diagnosed == ["600519", "000001"]


def test_batch_limit_five(setup):
    router, pipe = setup
    reply = router.handle(msg("批量 茅台 宁德时代 比亚迪 平安 招商 中远 万科"))
    assert pipe.diagnosed == ["600519", "300750", "002594", "000001", "001872"]
    assert "5" in reply and "万科" not in reply.replace("前 5", "")


def test_batch_partial_failure(setup):
    router, pipe = setup
    pipe.fail = {"300750"}
    reply = router.handle(msg("批量 茅台 宁德时代 比亚迪"))
    assert pipe.diagnosed == ["600519", "300750", "002594"]
    assert "BOOM" in reply and "贵州茅台" in reply and "比亚迪" in reply


def test_batch_unresolvable_listed_not_crash(setup):
    router, pipe = setup
    reply = router.handle(msg("批量 茅台 不存在的票"))
    assert pipe.diagnosed == ["600519"]
    assert "不存在的票" in reply


def test_batch_without_args_prompts(setup):
    router, pipe = setup
    reply = router.handle(msg("批量"))
    assert reply and pipe.diagnosed == []


# ---------- 历史 ----------

def test_history_lists_rows(setup):
    router, pipe = setup
    pipe.history_rows["600519"] = [
        {"created_at": "2026-09-26 15:30", "trade_date": "2026-09-26", "action": "buy", "score": 72.0, "summary": "放量突破ABC"},
        {"created_at": "2026-09-25 15:30", "trade_date": "2026-09-25", "action": "hold", "score": 55.0, "summary": "观望DEF"},
    ]
    reply = router.handle(msg("历史 茅台"))
    assert pipe.history_calls == [("600519", 5)]
    assert "放量突破ABC" in reply and "观望DEF" in reply and "2026-09-26" in reply


def test_history_empty(setup):
    router, pipe = setup
    reply = router.handle(msg("历史茅台"))
    assert pipe.history_calls == [("600519", 5)]
    assert reply and "贵州茅台" in reply or "没有" in reply or "暂无" in reply


def test_history_unparseable_goes_to_chat(setup):
    router, pipe = setup
    reply = router.handle(msg("历史上茅台涨过几次停"))
    assert pipe.history_calls == []
    assert Chat.instances[0].calls[0][0] == "历史上茅台涨过几次停"
    assert reply.startswith("答:")


# ---------- 权限 ----------

def test_allowed_users_still_enforced(monkeypatch, custom_dir):
    monkeypatch.setattr(watchlist_module.WatchlistService, "resolve", lambda self, text: STOCKS.get(text))
    pipe = Pipe()
    router = CommandRouter({"database": {"sqlite_path": ":memory:"}, "bot": {"allowed_users": ["u1"]}},
                           pipeline=pipe, chat_factory=Chat)
    for text in ("策略龙回头 茅台", "批量 茅台", "历史 茅台", "策略"):
        assert "没有使用权限" in router.handle(msg(text, user="intruder"))
    assert pipe.diagnosed == [] and pipe.history_calls == []
    assert "没有使用权限" not in router.handle(msg("批量 茅台", user="u1"))
