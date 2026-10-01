"""自定义报告模板（Jinja2）：渲染、回退、沙箱、保存校验、接入点与接口。"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from src.database import db as db_module
from src.database.db import init_db
from tests.test_api import env  # noqa: F401

rt = pytest.importorskip("src.services.report_templates", reason="report_templates 尚未实现")

NAMES = ["diagnosis", "watchlist", "market_review", "daily_report"]

DIAG = {
    "code": "600519", "name": "贵州茅台", "action": "buy", "action_label": "买入", "score": 78,
    "one_sentence": "趋势向上", "summary": "摘要", "battle_plan": {"buy_zone": "1500-1520"},
    "risks": ["估值偏高"], "catalysts": ["提价"], "guardrails": [], "confidence": "中",
    "created_at": "2026-09-25 16:30", "trade_date": "2026-09-25", "language": "zh",
    "position_advice": {"no_position": "观望", "has_position": "持有"}, "theme_role": {}, "market_regime": "均衡",
    "checklist": [], "analysis": "分析",
}


@pytest.fixture
def tpl_dir(tmp_path, monkeypatch):
    """把模板目录指向临时目录（兼容开发可能使用的几种定位函数名）。"""
    d = tmp_path / "templates"
    d.mkdir()
    done = False
    for attr in ("templates_dir", "template_dir", "get_templates_dir", "_templates_dir", "_template_dir"):
        if hasattr(rt, attr):
            monkeypatch.setattr(rt, attr, lambda *a, **k: d)
            done = True
            break
    if not done:
        pytest.fail("找不到模板目录定位函数（templates_dir / template_dir ...），请按实际函数名调整测试")
    if hasattr(rt, "reset_cache"):
        rt.reset_cache()
    return d


def _write(d: Path, name: str, text: str) -> Path:
    p = d / f"{name}.md.j2"
    p.write_text(text, encoding="utf-8")
    return p


# ---------- render_report ----------

def test_template_names():
    assert rt.TEMPLATE_NAMES == {"diagnosis": "个股诊断", "watchlist": "自选股决策仪表盘",
                                 "market_review": "大盘复盘", "daily_report": "日报"}


def test_no_template_returns_fallback(tpl_dir):
    assert rt.render_report("diagnosis", DIAG, "原文\n  内容 ") == "原文\n  内容 "


def test_renders_variables_and_default(tpl_dir):
    _write(tpl_dir, "diagnosis", "# {{ name }}({{ code }}) {{ action_label }} {{ score }}\n{{ one_sentence }}\n---\n{{ default }}")
    out = rt.render_report("diagnosis", DIAG, "FALLBACK")
    assert out.startswith("# 贵州茅台(600519) 买入 78\n趋势向上\n---\nFALLBACK")


def test_trim_blocks_and_missing_variable(tpl_dir):
    _write(tpl_dir, "diagnosis", "{% for r in risks %}\n- {{ r }}\n{% endfor %}\n{{ nothing.deep.chain }}x")
    out = rt.render_report("diagnosis", DIAG, "FB")
    assert out == "- 估值偏高\nx"       # trim_blocks / lstrip_blocks，缺失变量链式为空


def test_no_autoescape(tpl_dir):
    _write(tpl_dir, "diagnosis", "{{ v }}")
    assert rt.render_report("diagnosis", {"v": "<b>&</b>"}, "FB") == "<b>&</b>"


@pytest.mark.parametrize("text", [
    "{% if %}坏",                      # 语法错误
    "{{ 1 / 0 }}",                      # 运行时错误
    "{{ score.foo() }}",                # 调用不存在的方法
    "   \n\n  ",                        # 空白结果
    "{# 只有注释 #}",
])
def test_error_or_blank_falls_back(tpl_dir, text):
    from loguru import logger

    logs: list[str] = []
    sink = logger.add(lambda m: logs.append(str(m)), level="WARNING")
    try:
        _write(tpl_dir, "diagnosis", text)
        assert rt.render_report("diagnosis", DIAG, "FB") == "FB"
    finally:
        logger.remove(sink)
    assert any("diagnosis" in m for m in logs)      # 记了 warning


def test_cache_invalidated_on_mtime(tpl_dir):
    p = _write(tpl_dir, "diagnosis", "第一版 {{ name }}")
    assert rt.render_report("diagnosis", DIAG, "FB") == "第一版 贵州茅台"
    p.write_text("第二版 {{ name }}", encoding="utf-8")
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert rt.render_report("diagnosis", DIAG, "FB") == "第二版 贵州茅台"


def test_deleted_file_reverts_to_fallback(tpl_dir):
    p = _write(tpl_dir, "diagnosis", "x{{ name }}")
    assert rt.render_report("diagnosis", DIAG, "FB") == "x贵州茅台"
    p.unlink()
    assert rt.render_report("diagnosis", DIAG, "FB") == "FB"


def test_unknown_name_returns_fallback(tpl_dir):
    _write(tpl_dir, "evil", "hacked")
    assert rt.render_report("evil", DIAG, "FB") == "FB"


# ---------- 沙箱 ----------

@pytest.mark.parametrize("expr", [
    "{{ ''.__class__.__mro__ }}",
    "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    "{{ self.__init__.__globals__ }}",
    "{{ cycler.__init__.__globals__.os.popen('echo pwned').read() }}",
    "{{ lipsum.__globals__ }}",
    "{{ name.__class__ }}",
])
def test_sandbox_blocks_dunder(tpl_dir, expr):
    _write(tpl_dir, "diagnosis", expr)
    out = rt.render_report("diagnosis", DIAG, "FB")
    assert out == "FB" and "pwned" not in out


def test_sandbox_blocks_dangerous_call_and_marker(tpl_dir, tmp_path):
    marker = tmp_path / "marker"
    _write(tpl_dir, "diagnosis",
           "{{ cycler.__init__.__globals__.os.system('touch " + str(marker) + "') }}")
    assert rt.render_report("diagnosis", DIAG, "FB") == "FB"
    assert not marker.exists()


def test_sandbox_blocks_mutation_of_data(tpl_dir):
    _write(tpl_dir, "diagnosis", "{% set _ = risks.append('x') %}ok")
    assert rt.render_report("diagnosis", {"risks": ["a"]}, "FB") == "FB"


def test_sandbox_blocks_include_of_files(tpl_dir, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET", encoding="utf-8")
    _write(tpl_dir, "diagnosis", "{% include '" + str(secret) + "' %}")
    assert "TOPSECRET" not in rt.render_report("diagnosis", DIAG, "FB")


# ---------- save / get / delete / list ----------

def test_list_custom_flag(tpl_dir):
    items = rt.list_templates()
    assert [i["name"] for i in items] == list(rt.TEMPLATE_NAMES) or {i["name"] for i in items} == set(NAMES)
    assert all(i["custom"] is False for i in items)
    assert {i["name"]: i["label"] for i in items} == rt.TEMPLATE_NAMES
    _write(tpl_dir, "watchlist", "x")
    items = {i["name"]: i for i in rt.list_templates()}
    assert items["watchlist"]["custom"] is True and items["diagnosis"]["custom"] is False
    assert "path" in items["watchlist"]


@pytest.mark.parametrize("name", NAMES)
def test_get_builtin_sample(tpl_dir, name):
    text = rt.get_template(name)
    assert text.strip() and "{#" in text and "#}" in text      # 开头有注释列出变量
    assert not (tpl_dir / f"{name}.md.j2").exists()


def test_save_get_delete(tpl_dir):
    rt.save_template("diagnosis", "自定义 {{ name }}")
    assert (tpl_dir / "diagnosis.md.j2").read_text(encoding="utf-8") == "自定义 {{ name }}"
    assert rt.get_template("diagnosis") == "自定义 {{ name }}"
    assert rt.render_report("diagnosis", DIAG, "FB") == "自定义 贵州茅台"
    rt.delete_template("diagnosis")
    assert not (tpl_dir / "diagnosis.md.j2").exists()
    assert rt.get_template("diagnosis") != "自定义 {{ name }}"
    assert rt.render_report("diagnosis", DIAG, "FB") == "FB"
    rt.delete_template("diagnosis")      # 重复删除不报错


def test_save_syntax_error_has_line(tpl_dir):
    with pytest.raises(ValueError) as e:
        rt.save_template("diagnosis", "第一行\n第二行\n{% if x %}\n没有结束")
    assert "4" in str(e.value) or "3" in str(e.value) or "行" in str(e.value)
    with pytest.raises(ValueError) as e:
        rt.save_template("diagnosis", "ok\n{{ a b }}")
    assert "2" in str(e.value)
    assert not (tpl_dir / "diagnosis.md.j2").exists()


def test_save_unknown_name(tpl_dir):
    with pytest.raises((ValueError, KeyError)):
        rt.save_template("../../evil", "x")
    with pytest.raises((ValueError, KeyError)):
        rt.save_template("nope", "x")
    assert list(tpl_dir.iterdir()) == []
    assert not (tpl_dir.parent.parent / "evil.md.j2").exists()


def test_save_empty_text(tpl_dir):
    # 空文本要么拒绝，要么保存后渲染回退，二者都不能抛出非 ValueError 的异常
    try:
        rt.save_template("diagnosis", "")
    except ValueError:
        return
    assert rt.render_report("diagnosis", DIAG, "FB") == "FB"


# ---------- 内置示例模板 + preview ----------

SAMPLE_DATA = {
    "diagnosis": DIAG,
    "watchlist": {"trade_date": "2026-09-25", "counts": {"买入": 1}, "failed": [{"code": "1", "name": "x", "error": "e"}],
                  "items": [{"code": "600519", "name": "贵州茅台", "action": "buy", "action_label": "买入", "score": 78,
                             "one_sentence": "好", "battle_plan": {}, "risks": [], "catalysts": [], "guardrails": [],
                             "change": "", "kind": "stock"}]},
    "market_review": {"trade_date": "2026-09-25", "headline": "标题", "trend": "t", "emotion": "e", "main_lines": "m",
                      "stance": "均衡", "position": "5 成", "focus": ["a"], "avoid": ["b"], "watch_points": ["c"],
                      "guardrails": [], "regime": "均衡"},
    "daily_report": {"title": "日报", "date": "2026-09-25", "market": "### 大盘", "sections": ["### 大盘", "> 免责"]},
}


@pytest.mark.parametrize("name", NAMES)
def test_builtin_sample_renders_with_data(tpl_dir, name):
    rt.save_template(name, rt.get_template(name))     # 内置示例本身语法合法
    out = rt.render_report(name, SAMPLE_DATA[name], "FALLBACK-" + name)
    assert out.strip()
    # 内置示例是按变量写的，渲染成功时不应退回 fallback
    assert out != "FALLBACK-" + name


@pytest.mark.parametrize("name", NAMES)
def test_preview_with_explicit_data(tpl_dir, name):
    out = rt.preview(name, rt.get_template(name), data=SAMPLE_DATA[name])
    assert isinstance(out, str) and out.strip()


def test_preview_syntax_error_raises_value_error(tpl_dir):
    with pytest.raises(ValueError):
        rt.preview("diagnosis", "{% if %}", data=DIAG)


def test_preview_does_not_write_file(tpl_dir):
    rt.preview("diagnosis", "x{{ name }}", data=DIAG)
    assert list(tpl_dir.iterdir()) == []


@pytest.mark.parametrize("name", NAMES)
def test_preview_without_data_uses_builtin_sample(tpl_dir, name, tmp_path, monkeypatch):
    # 没有数据时使用内置示例数据（库为空）；不报错、结果非空
    db_module._engine = None
    db_module._SessionFactory = None
    try:
        out = rt.preview(name, rt.get_template(name))
    finally:
        if db_module._engine:
            db_module._engine.dispose()
        db_module._engine = None
        db_module._SessionFactory = None
    assert isinstance(out, str) and out.strip()


# ---------- 接入点 ----------

def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def db_config(tmp_path):
    path = str(tmp_path / "tpl.db")
    _reset_db_engine()
    init_db(path)
    yield {"database": {"sqlite_path": path}, "llm": {}}
    _reset_db_engine()


class FakeDiag:
    def __init__(self):
        self.result = {"code": "600519", "name": "贵州茅台", "action": "buy", "action_label": "买入", "score": 80,
                       "one_sentence": "结论", "battle_plan": {}, "catalysts": [], "risks": [], "guardrails": [],
                       "created_at": "2026-09-25 16:30"}

    def latest(self, code, max_age_minutes=None):
        return self.result

    def history(self, code, limit=5):
        return []

    def diagnose(self, code, force=False):
        return self.result


def _run_watchlist(db_config, monkeypatch):
    from src.services import watchlist_report as wr
    from src.services.watchlist import WatchlistService

    monkeypatch.setattr(WatchlistService, "list", lambda self: [{"code": "600519", "name": "贵州茅台", "kind": "stock"}])
    return wr.WatchlistReportService(db_config, diagnosis=FakeDiag()).run(push=False, now=datetime(2026, 9, 25, 16, 30))


def test_watchlist_uses_template(db_config, tpl_dir, monkeypatch):
    plain = _run_watchlist(db_config, monkeypatch)["markdown"]
    _write(tpl_dir, "watchlist", "DASH {{ trade_date }} {{ items|length }} {{ counts|length }} {{ failed|length }}")
    result = _run_watchlist(db_config, monkeypatch)
    assert result["markdown"].startswith("DASH 2026-09-25 1 ")
    assert result["markdown"] != plain
    from src.services.watchlist_report import WatchlistReportService
    assert "DASH" in WatchlistReportService(db_config).latest()["markdown"]      # 入库的也是模板输出


def test_watchlist_bad_template_same_as_builtin(db_config, tpl_dir, monkeypatch):
    plain = _run_watchlist(db_config, monkeypatch)["markdown"]
    _write(tpl_dir, "watchlist", "{{ 1/0 }}")
    broken = _run_watchlist(db_config, monkeypatch)["markdown"]
    strip = lambda s: "\n".join(line for line in s.splitlines() if "Generated" not in line and "生成" not in line)  # noqa: E731
    assert strip(broken) == strip(plain)


def test_market_review_uses_template(tpl_dir):
    from src.services.market_context import MarketFacts
    from src.services.market_review import MarketReviewService, render_markdown

    raw = {"headline": "缩量调整", "trend": "回落", "emotion": "冷", "main_lines": "无", "stance": "均衡",
           "position": "5 成", "focus": ["海峡"], "avoid": ["高位"], "watch_points": ["x"]}
    plain = MarketReviewService._apply_guardrails(raw, MarketFacts(), "2026-09-25")
    assert plain["markdown"] == render_markdown(plain)      # 无模板：逐字相同
    _write(tpl_dir, "market_review", "复盘 {{ headline }} / {{ stance }} / {{ trade_date }}")
    custom = MarketReviewService._apply_guardrails(raw, MarketFacts(), "2026-09-25")
    assert custom["markdown"] == "复盘 缩量调整 / 均衡 / 2026-09-25"


@pytest.fixture
def bot_router(monkeypatch):
    from src.bot.router import CommandRouter
    from src.services import watchlist as watchlist_module
    from tests.test_bot import FakeChat, FakePipeline, STOCKS

    class P(FakePipeline):
        def diagnose_stock(self, code, force=False):
            return {**DIAG, "code": code}

    monkeypatch.setattr(watchlist_module.WatchlistService, "resolve", lambda self, text: STOCKS.get(text))
    return CommandRouter({"database": {"sqlite_path": ":memory:"}}, pipeline=P(), chat_factory=FakeChat)


def _msg(text):
    from src.bot.models import BotMessage
    return BotMessage(platform="dingtalk", chat_id="c", user_id="u", user_name="n", text=text)


def test_bot_diagnosis_uses_template(bot_router, tpl_dir):
    from src.services.stock_diagnosis import render_markdown

    assert bot_router.handle(_msg("诊断 茅台")) == render_markdown({**DIAG, "code": "600519"})
    _write(tpl_dir, "diagnosis", "BOT {{ name }} {{ action_label }}")
    assert bot_router.handle(_msg("诊断 茅台")) == "BOT 贵州茅台 买入"
    _write(tpl_dir, "diagnosis", "{% if %}")
    assert bot_router.handle(_msg("诊断 茅台")) == render_markdown({**DIAG, "code": "600519"})


def test_download_and_image_use_template(env, tpl_dir, monkeypatch):
    import json
    from src.database.db import get_db_session
    from src.database.models import StockDiagnosis
    from src.services import report_image

    client, app, config = env
    with get_db_session(config["database"]["sqlite_path"]) as s:
        row = StockDiagnosis(code="600519", name="贵州茅台", trade_date="2026-09-25", action="buy", score=78,
                             result_json=json.dumps(DIAG, ensure_ascii=False), created_at=datetime.now())
        s.add(row)
        s.flush()
        rid = row.id
    r = client.get(f"/api/v1/stocks/diagnoses/{rid}/markdown")
    assert r.status_code == 200 and "DL-" not in r.text
    _write(tpl_dir, "diagnosis", "DL-{{ name }}-{{ score }}")
    r = client.get(f"/api/v1/stocks/diagnoses/{rid}/markdown")
    assert "DL-贵州茅台-78" in r.text
    captured = {}
    monkeypatch.setattr(report_image, "render_markdown_image",
                        lambda title, body, **kw: captured.update(body=body) or b"\x89PNG\r\n\x1a\nfake")
    r = client.get(f"/api/v1/stocks/diagnoses/{rid}/image")
    assert r.status_code == 200 and "DL-贵州茅台-78" in captured["body"]


# ---------- API ----------

def test_api_list_and_get(env, tpl_dir):
    client, _, _ = env
    r = client.get("/api/v1/settings/templates")
    assert r.status_code == 200
    items = {i["name"]: i for i in r.json()}
    assert set(items) == set(NAMES) and not any(i["custom"] for i in items.values())
    r = client.get("/api/v1/settings/templates/diagnosis")
    assert r.status_code == 200
    body = r.json()
    assert body["custom"] is False and "{#" in body["text"]


def test_api_put_get_delete(env, tpl_dir):
    client, _, _ = env
    r = client.put("/api/v1/settings/templates/diagnosis", json={"text": "X {{ name }}"})
    assert r.status_code == 200
    got = client.get("/api/v1/settings/templates/diagnosis").json()
    assert got["custom"] is True and got["text"] == "X {{ name }}"
    assert {i["name"]: i["custom"] for i in client.get("/api/v1/settings/templates").json()}["diagnosis"] is True
    assert client.delete("/api/v1/settings/templates/diagnosis").status_code == 200
    got = client.get("/api/v1/settings/templates/diagnosis").json()
    assert got["custom"] is False and got["text"] != "X {{ name }}"


def test_api_put_syntax_error_400(env, tpl_dir):
    client, _, _ = env
    r = client.put("/api/v1/settings/templates/diagnosis", json={"text": "a\nb\n{% for %}"})
    assert r.status_code == 400 and "3" in r.text
    assert not (tpl_dir / "diagnosis.md.j2").exists()


def test_api_preview(env, tpl_dir):
    client, _, _ = env
    r = client.post("/api/v1/settings/templates/market_review/preview", json={"text": "预览 {{ stance }}"})
    assert r.status_code == 200
    assert "预览" in r.text
    bad = client.post("/api/v1/settings/templates/market_review/preview", json={"text": "{% if %}"})
    assert bad.status_code in (200, 400) and ("error" in bad.text or bad.status_code == 400)
    assert list(tpl_dir.iterdir()) == []


@pytest.mark.parametrize("method,suffix", [("get", ""), ("put", ""), ("delete", ""), ("post", "/preview")])
def test_api_unknown_name_404(env, tpl_dir, method, suffix):
    client, _, _ = env
    kwargs = {"json": {"text": "x"}} if method in ("put", "post") else {}
    r = getattr(client, method)(f"/api/v1/settings/templates/nope{suffix}", **kwargs)
    assert r.status_code == 404
    assert getattr(client, method)(f"/api/v1/settings/templates/..%2Fevil{suffix}", **kwargs).status_code in (404, 405)
    assert list(tpl_dir.iterdir()) == []


def test_requirements_has_jinja2():
    text = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "jinja2" in text


def test_build_desktop_bundles_templates():
    text = (Path(__file__).resolve().parents[1] / "scripts" / "build_desktop.py").read_text(encoding="utf-8")
    assert "services/templates" in text.replace("\\", "/") or "services\" / \"templates" in text or "templates" in text
