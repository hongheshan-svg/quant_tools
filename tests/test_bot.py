"""聊天机器人：命令分发、会话、消息解析、去重和启动（不联网、不依赖钉钉/飞书 SDK 的连接）"""

import json
import time
from types import SimpleNamespace

import pytest

from src.bot import dingtalk, feishu, manager
from src.bot.dispatcher import Dispatcher, reply_title
from src.bot.models import BotMessage
from src.bot.router import HELP_TEXT, CommandRouter
from src.services import watchlist as watchlist_module

STOCKS = {"茅台": ("600519", "贵州茅台"), "600519": ("600519", "贵州茅台"), "宁德": ("300750", "宁德时代")}


class FakePipeline:
    db_path = ":memory:"

    def __init__(self):
        self.watch = [{"code": "600519", "name": "贵州茅台", "close": 1713.28, "change_pct": -0.54,
                       "diagnosis": {"action_label": "持有", "score": 62}}]
        self.diagnosed = []

    def diagnose_stock(self, code, force=False):
        self.diagnosed.append(code)
        return {"code": code, "name": "贵州茅台", "action_label": "持有"}

    def latest_market_review(self):
        return None

    def market_regime(self):
        return {"regime": "均衡", "summary": "市场环境：均衡（55分）"}

    def watchlist_overview(self):
        return self.watch

    def watchlist_add(self, text):
        code, name = STOCKS[text]
        return {"ok": True, "code": code, "name": name}

    def watchlist_remove(self, code):
        return code == "600519"

    def trading_snapshot(self):
        return {"positions": [{"code": "601919", "name": "中远海控", "quantity": 1000, "avg_cost": 15.8,
                               "market_price": 17.58, "unrealized_pnl": 1780.0}]}

    def data_source_status(self):
        return [{"dataset": "实时行情", "source": "腾讯", "consecutive_failures": 0},
                {"dataset": "涨停池", "source": "东方财富", "consecutive_failures": 3, "last_error": "timeout"}]


class FakeChat:
    created = 0

    def __init__(self):
        FakeChat.created += 1
        self.questions = []

    def ask(self, question, perspective="综合", progress=None):
        self.questions.append(question)
        if progress:
            progress("正在查询：行情")
            progress("正在查询：资金流")
        return SimpleNamespace(answer=f"回答{len(self.questions)}：{question}", error="")


@pytest.fixture
def router(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistService, "resolve", lambda self, text: STOCKS.get(text))
    FakeChat.created = 0
    return CommandRouter({"database": {"sqlite_path": ":memory:"}}, pipeline=FakePipeline(), chat_factory=FakeChat)


def msg(text, user="u1", chat="c1", **kw):
    return BotMessage(platform="dingtalk", chat_id=chat, user_id=user, user_name="张三", text=text, **kw)


@pytest.mark.parametrize("text,command,args", [
    ("诊断茅台", "诊断", "茅台"),
    ("分析 600519", "诊断", "600519"),
    ("/analyze 600519", "诊断", "600519"),
    ("大盘", "大盘", ""),
    ("/market", "大盘", ""),
    ("自选 加 茅台", "自选股", "加 茅台"),
    ("自选股", "自选股", ""),
    ("HELP", "帮助", ""),
    ("大盘怎么样", None, ""),          # 不带参数的命令要整句匹配，其余交给问股
    ("/analyzer", None, ""),
    ("宁德时代最近资金流怎么样", None, ""),
])
def test_match(router, text, command, args):
    matched, rest = router.match(text)
    assert (matched.names[0] if matched else None) == command
    assert rest == args


def test_help_and_empty_message(router):
    assert router.handle(msg("帮助")) == HELP_TEXT
    assert router.handle(msg("  ")) == HELP_TEXT


def test_allowed_users(monkeypatch):
    r = CommandRouter({"bot": {"allowed_users": ["boss"]}}, pipeline=FakePipeline(), chat_factory=FakeChat)
    reply = r.handle(msg("大盘", user="intruder"))
    assert "没有使用权限" in reply and "intruder" in reply
    assert "均衡" in r.handle(msg("大盘", user="boss"))


def test_diagnose_resolves_stock_and_reports_progress(router, monkeypatch):
    from src.services import stock_diagnosis

    monkeypatch.setattr(stock_diagnosis, "render_markdown", lambda result: f"# {result['name']} {result['action_label']}")
    progress = []
    reply = router.handle(msg("诊断 茅台"), progress=progress.append)
    assert reply == "# 贵州茅台 持有"
    assert router.pipeline.diagnosed == ["600519"]
    assert progress and "贵州茅台(600519)" in progress[0]


def test_diagnose_without_stock_falls_back_to_chat(router):
    assert router.handle(msg("诊断")) == "请带上股票，例如：诊断 茅台"
    reply = router.handle(msg("分析一下最近的行情"))
    assert reply.startswith("回答1：分析一下最近的行情")


def test_market_without_review_shows_regime(router):
    reply = router.handle(msg("大盘"))
    assert "市场环境：均衡（55分）" in reply and "还没有生成大盘复盘" in reply


def test_watchlist_commands(router):
    assert "贵州茅台(600519) 1713.28 -0.54% ｜ 持有 62分" in router.handle(msg("自选")).replace("**", "")
    assert router.handle(msg("自选 加 宁德")) == "已加入自选：宁德时代(300750)"
    assert router.handle(msg("自选 删 茅台")) == "已移出自选：贵州茅台(600519)"
    assert router.handle(msg("自选 删 宁德")) == "宁德时代(300750) 不在自选股中"
    assert router.handle(msg("自选 删 不存在")) == "找不到股票「不存在」"


def test_positions_and_status(router, monkeypatch):
    from src.services import real_portfolio

    monkeypatch.setattr(real_portfolio.RealPortfolioService, "snapshot", lambda self: {"positions": []})
    reply = router.handle(msg("持仓"))
    assert "模拟盘持仓（1）" in reply and "中远海控" in reply and "+1,780" in reply
    assert "实盘记账" not in reply
    status = router.handle(msg("状态"))
    assert "共 2 个数据源，1 个正常" in status and "涨停池 / 东方财富" in status


def test_chat_sessions_keep_context_per_user_and_expire(router):
    progress = []
    assert router.handle(msg("茅台怎么样"), progress=progress.append) == "回答1：茅台怎么样"
    assert progress == ["正在查询：行情…"]  # 多次工具调用只提示一次
    assert router.handle(msg("那宁德呢")) == "回答2：那宁德呢"
    assert router.handle(msg("你好", user="u2")) == "回答1：你好"  # 群里每个人各自一段对话
    assert FakeChat.created == 2
    assert "已结束" in router.handle(msg("清空"))
    assert router.handle(msg("再问")) == "回答1：再问"
    router.idle_seconds = 0
    time.sleep(0.01)
    assert router.handle(msg("过期后")) == "回答1：过期后"


def test_dispatcher_dedupes_and_replies(router):
    replies = []
    dispatcher = Dispatcher(router, workers=1)
    first = dispatcher.submit(msg("大盘", message_id="m1"), replies.append)
    assert dispatcher.submit(msg("大盘", message_id="m1"), replies.append) is None
    first.result(timeout=5)
    assert len(replies) == 1 and "均衡" in replies[0]


def test_dispatcher_reports_router_crash():
    class Boom:
        def handle(self, message, progress=None):
            raise RuntimeError("炸了")

    replies = []
    Dispatcher(Boom()).submit(msg("x"), replies.append).result(timeout=5)
    assert replies == ["处理失败：炸了"]


def test_reply_title():
    assert reply_title("### 自选股（3）\n- a") == "自选股（3）"
    assert reply_title("") == "A股量化"


def test_dingtalk_parse_message():
    data = {"msgtype": "text", "text": {"content": "@A股助手 诊断 茅台"}, "conversationId": "cid", "conversationType": "2",
            "senderStaffId": "staff1", "senderId": "$:id", "senderNick": "张三", "msgId": "mid"}
    m = dingtalk.parse_message(data)
    assert (m.text, m.is_group, m.user_id, m.chat_id, m.message_id) == ("诊断 茅台", True, "staff1", "cid", "mid")
    assert dingtalk.parse_message({**data, "msgtype": "picture"}) is None
    single = dingtalk.parse_message({**data, "conversationType": "1", "senderStaffId": None, "text": {"content": "大盘"}})
    assert (single.is_group, single.user_id, single.text) == (False, "$:id", "大盘")


def _feishu_message(text, chat_type="p2p", mentions=None, message_type="text"):
    return SimpleNamespace(message_type=message_type, content=json.dumps({"text": text}), chat_type=chat_type,
                           mentions=mentions, chat_id="oc_1", message_id="om_1")


def test_feishu_parse_message():
    sender = SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_1", user_id=None))
    m = feishu.parse_message(_feishu_message("@_user_1 诊断 茅台", "group", [SimpleNamespace(key="@_user_1")]), sender)
    assert (m.text, m.is_group, m.user_id, m.message_id) == ("诊断 茅台", True, "ou_1", "om_1")
    assert feishu.parse_message(_feishu_message("大家好", "group", []), sender) is None  # 群里没 @机器人
    assert feishu.parse_message(_feishu_message("x", message_type="image"), sender) is None
    assert feishu.parse_message(_feishu_message("大盘"), sender).text == "大盘"


def test_feishu_card_converts_headings():
    card = json.loads(feishu.to_card("### 自选股\n- **茅台**"))
    assert card["elements"][0]["content"] == "**自选股**\n- **茅台**"


@pytest.fixture
def clean_manager(monkeypatch):
    monkeypatch.setattr(manager, "_running", {})
    return manager


def test_start_bots_skips_disabled_and_incomplete(clean_manager):
    assert clean_manager.start_bots({}) == []
    assert clean_manager.start_bots({"bot": {"dingtalk": {"enabled": True, "client_id": "", "client_secret": "x"}}}) == []


def test_start_bots_starts_once(clean_manager, monkeypatch):
    started = []

    class FakeBot:
        def __init__(self, client_id, client_secret, dispatcher):
            self.client_id = client_id

        def start(self):
            started.append(self.client_id)

    monkeypatch.setattr(dingtalk, "DingTalkBot", FakeBot)
    config = {"bot": {"dingtalk": {"enabled": True, "client_id": "key", "client_secret": "secret"}}}
    assert clean_manager.start_bots(config, pipeline=FakePipeline()) == ["dingtalk"]
    assert clean_manager.start_bots(config, pipeline=FakePipeline()) == []
    assert started == ["key"] and clean_manager.running_bots() == ["dingtalk"]


def test_retry_delay_backs_off_to_ten_minutes():
    from src.bot.dispatcher import retry_delay

    assert [retry_delay(n) for n in (0, 1, 2, 3)] == [10, 10, 20, 40]
    assert retry_delay(20) == 600
