"""WS30 分享图品牌与大盘复盘/自选股仪表盘分享图：HTML 构建、选项、缓存、推送、接口。全部离线，不启动浏览器。"""

from __future__ import annotations

import json
import re

import pytest

from src import notifier
from src.database.db import get_db_session
from src.database.models import MarketReview, StockDiagnosis, WatchlistReport
from src.services import report_image
from tests.test_api import env  # noqa: F401  (pytest fixture)

PNG = b"\x89PNG\r\n\x1a\nfake"
DEFAULT_FOOTER = "仅供学习研究，不构成投资建议"
DIAG_FOOTER = "AI 诊断仅供参考，不构成投资建议"


@pytest.fixture
def rendered(monkeypatch):
    """替换 render_png，记录收到的 html；同时清空渲染缓存。"""
    htmls: list[str] = []

    def fake(html, width=760):
        htmls.append(html)
        return PNG

    monkeypatch.setattr(report_image, "render_png", fake)
    cache = getattr(report_image, "_CACHE", None)
    if hasattr(cache, "clear"):
        cache.clear()
    return htmls


# ---------- build_share_html ----------

def test_brand_bar_shown_above_title():
    html = report_image.build_share_html("日报标题", "- x", "页脚", brand="量化小站")
    assert "量化小站" in html
    assert html.index("量化小站") < html.rindex("日报标题")


def test_no_brand_no_brand_text():
    html = report_image.build_share_html("日报标题", "- x", "页脚")
    assert "量化小站" not in html and "日报标题" in html and "页脚" in html


def test_footer_shown():
    assert "自定义页脚" in report_image.build_share_html("T", "m", "自定义页脚", brand="B")


def test_brand_and_footer_escaped():
    html = report_image.build_share_html("T", "m", "<img src=x onerror=1>", brand="<script>alert(1)</script>")
    assert "<script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html


def test_qr_url_escaped_and_non_http_ignored():
    pytest.importorskip("qrcode")
    for bad in ("javascript:alert(1)", "ftp://a.b/c", "file:///etc/passwd", "weixin://x", "just text"):
        html = report_image.build_share_html("T", "m", "f", qr_url=bad)
        assert "data:image/png;base64" not in html, bad
        assert bad not in html


def test_qr_data_uri_embedded():
    pytest.importorskip("qrcode")
    html = report_image.build_share_html("T", "m", "页脚", qr_url="https://example.com/me?a=1&b=2")
    m = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)", html)
    assert m, "应内嵌二维码 data URI"
    import base64
    assert base64.b64decode(m.group(1))[:8] == b"\x89PNG\r\n\x1a\n"
    assert html.index("页脚") < html.index("data:image/png") or "页脚" in html


def test_no_qr_without_url():
    html = report_image.build_share_html("T", "m", "f", brand="B")
    assert "data:image/png;base64" not in html


def test_qr_missing_library_degrades(monkeypatch):
    """未安装 qrcode 时不显示二维码也不报错。"""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "qrcode" or name.startswith("qrcode."):
            raise ImportError("no qrcode")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    html = report_image.build_share_html("T", "m", "f", qr_url="https://example.com")
    assert "data:image/png;base64" not in html and "T" in html


def test_backward_compatible_signature():
    assert "标题" in report_image.build_share_html("标题", "", "")
    assert "标题" in report_image.build_share_html("标题", "")


# ---------- share_options ----------

def test_share_options_defaults():
    for cfg in ({}, {"notifier": {}}, {"notifier": {"image": {}}}, {"notifier": {"image": None}}):
        opts = report_image.share_options(cfg)
        assert opts["brand"] == "" and opts["qr_url"] == ""
        assert opts["footer"] == DEFAULT_FOOTER


def test_share_options_override():
    cfg = {"notifier": {"image": {"brand": "我的号", "footer": "自定义", "qr_url": "https://x.y/z", "channels": ["wechat"]}}}
    assert report_image.share_options(cfg) == {"brand": "我的号", "footer": "自定义", "qr_url": "https://x.y/z"}


def test_share_options_none_values_safe():
    opts = report_image.share_options({"notifier": {"image": {"brand": None, "qr_url": None}}})
    assert opts["brand"] == "" and opts["qr_url"] == ""


def test_share_options_usable_as_kwargs(rendered):
    opts = report_image.share_options({"notifier": {"image": {"brand": "B1"}}})
    report_image.render_markdown_image("T", "m", **opts)
    assert "B1" in rendered[0]


# ---------- 渲染缓存 ----------

def test_cache_key_distinguishes_brand_params(rendered):
    report_image.render_markdown_image("T", "m")
    report_image.render_markdown_image("T", "m")
    assert len(rendered) == 1
    report_image.render_markdown_image("T", "m", brand="A")
    assert len(rendered) == 2
    report_image.render_markdown_image("T", "m", brand="A")
    assert len(rendered) == 2
    report_image.render_markdown_image("T", "m", brand="B")
    report_image.render_markdown_image("T", "m", footer="F")
    report_image.render_markdown_image("T", "m", qr_url="https://a.b")
    assert len(rendered) == 5
    report_image.render_markdown_image("T", "m", qr_url="https://c.d")
    assert len(rendered) == 6


def test_render_passes_brand_to_html(rendered):
    report_image.render_markdown_image("标题", "## 正文", footer="页脚X", brand="品牌Y")
    assert "品牌Y" in rendered[0] and "页脚X" in rendered[0] and "标题" in rendered[0]


# ---------- 推送分享图使用品牌 ----------

class _Chan:
    log: list = []

    def __init__(self, config):
        pass

    def send(self, title, content):
        _Chan.log.append(("send", title))
        return True

    def send_image(self, title, png, *a, **k):
        _Chan.log.append(("image", title, png))
        return True


def _cfg(**image):
    base = {"channels": ["wechat"], "kinds": ["daily_report"], "max_chars": 500}
    base.update(image)
    return {"notifier": {
        "wechat": {"enabled": True, "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc"},
        "image": base}}


@pytest.fixture
def chan(monkeypatch, rendered):
    _Chan.log = []
    monkeypatch.setitem(notifier.NOTIFIERS, "wechat", _Chan)
    monkeypatch.delenv("QUANT_NO_NOTIFY", raising=False)
    return rendered


def test_push_image_uses_brand(chan):
    result = notifier.broadcast(_cfg(brand="推送品牌", footer="推送页脚"), "日报", "简短", kind="daily_report")
    assert result.get("wechat") is True
    assert [e[0] for e in _Chan.log] == ["image"] and _Chan.log[0][2] == PNG
    assert "推送品牌" in chan[0] and "推送页脚" in chan[0]


def test_push_image_default_footer_without_brand(chan):
    notifier.broadcast(_cfg(), "日报", "简短", kind="daily_report")
    assert DEFAULT_FOOTER in chan[0]


def test_push_image_brand_escaped(chan):
    notifier.broadcast(_cfg(brand="<script>x</script>"), "日报", "简短", kind="daily_report")
    assert "<script>x</script>" not in chan[0]


def test_push_image_qr(chan):
    pytest.importorskip("qrcode")
    notifier.broadcast(_cfg(qr_url="https://example.com/me"), "日报", "简短", kind="daily_report")
    assert "data:image/png;base64" in chan[0]


# ---------- 接口 ----------

def _add_review(config, date, md="## 复盘\n\n- 看多"):
    with get_db_session(config["database"]["sqlite_path"]) as s:
        s.add(MarketReview(trade_date=date, stance="均衡",
                           content_json=json.dumps({"trade_date": date, "stance": "均衡", "markdown": md}, ensure_ascii=False),
                           markdown=md))


def _add_report(config, date, md="## 仪表盘\n\n- 茅台 买入"):
    with get_db_session(config["database"]["sqlite_path"]) as s:
        s.add(WatchlistReport(trade_date=date, markdown=md, summary_json=json.dumps({"items": [], "failed": []})))


def test_review_image_ok_latest(env, rendered):
    client, _, config = env
    _add_review(config, "2026-09-24", "## 旧复盘")
    _add_review(config, "2026-09-25", "## 新复盘内容")
    r = client.get("/api/v1/market/review/image")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    assert r.content == PNG
    assert "2026-09-25 大盘复盘" in rendered[0] and "新复盘内容" in rendered[0]
    assert "旧复盘" not in rendered[0]


def test_review_image_specific_date(env, rendered):
    client, _, config = env
    _add_review(config, "2026-09-24", "## 旧复盘内容")
    _add_review(config, "2026-09-25", "## 新复盘")
    r = client.get("/api/v1/market/review/image", params={"trade_date": "2026-09-24"})
    assert r.status_code == 200
    assert "2026-09-24 大盘复盘" in rendered[0] and "旧复盘内容" in rendered[0]


def test_review_image_not_found(env, rendered):
    client, _, config = env
    assert client.get("/api/v1/market/review/image").status_code == 404
    _add_review(config, "2026-09-25")
    assert client.get("/api/v1/market/review/image", params={"trade_date": "2026-01-01"}).status_code == 404
    assert rendered == []


def test_review_image_render_failure_503(env, monkeypatch):
    client, _, config = env
    _add_review(config, "2026-09-25")

    def boom(*a, **k):
        raise RuntimeError("no chromium")

    monkeypatch.setattr(report_image, "render_png", boom)
    report_image._CACHE.clear() if hasattr(getattr(report_image, "_CACHE", None), "clear") else None
    assert client.get("/api/v1/market/review/image").status_code == 503


def test_review_image_uses_brand(env, rendered):
    client, app, config = env
    config["notifier"]["image"] = {"brand": "复盘品牌", "footer": "复盘页脚", "qr_url": ""}
    _add_review(config, "2026-09-25")
    r = client.get("/api/v1/market/review/image")
    assert r.status_code == 200
    assert "复盘品牌" in rendered[0] and "复盘页脚" in rendered[0]


def test_watchlist_report_image_ok_latest(env, rendered):
    client, _, config = env
    _add_report(config, "2026-09-24", "## 旧仪表盘")
    _add_report(config, "2026-09-25", "## 新仪表盘内容")
    r = client.get("/api/v1/watchlist/report/image")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    assert r.content == PNG
    assert "新仪表盘内容" in rendered[0] and "旧仪表盘" not in rendered[0]


def test_watchlist_report_image_not_found(env, rendered):
    client, _, _ = env
    assert client.get("/api/v1/watchlist/report/image").status_code == 404
    assert rendered == []


def test_watchlist_report_image_failure_503(env, monkeypatch):
    client, _, config = env
    _add_report(config, "2026-09-25")

    def boom(*a, **k):
        raise RuntimeError("no chromium")

    monkeypatch.setattr(report_image, "render_png", boom)
    assert client.get("/api/v1/watchlist/report/image").status_code == 503


def test_watchlist_report_image_uses_brand(env, rendered):
    client, _, config = env
    config["notifier"]["image"] = {"brand": "自选品牌"}
    _add_report(config, "2026-09-25")
    assert client.get("/api/v1/watchlist/report/image").status_code == 200
    assert "自选品牌" in rendered[0]


def test_existing_report_route_not_shadowed(env):
    client, _, config = env
    assert client.get("/api/v1/watchlist/report").status_code == 200
    _add_review(config, "2026-09-25")
    assert client.get("/api/v1/market/review").status_code == 200


def _add_diag(config):
    with get_db_session(config["database"]["sqlite_path"]) as s:
        row = StockDiagnosis(code="600519", name="贵州茅台", trade_date="2026-09-25", action="buy", score=80,
                             result_json=json.dumps({"action": "buy", "score": 80, "one_sentence": "结论", "code": "600519", "name": "贵州茅台"},
                                                    ensure_ascii=False))
        s.add(row)
        s.flush()
        return row.id


def test_diagnosis_image_uses_brand_footer(env, rendered):
    client, _, config = env
    config["notifier"]["image"] = {"brand": "诊断品牌", "footer": "诊断自定义页脚"}
    did = _add_diag(config)
    r = client.get(f"/api/v1/stocks/diagnoses/{did}/image")
    assert r.status_code == 200
    assert "诊断品牌" in rendered[0] and "诊断自定义页脚" in rendered[0]


def test_diagnosis_image_empty_footer_falls_back(env, rendered):
    client, _, config = env
    config["notifier"]["image"] = {"brand": "B", "footer": ""}
    did = _add_diag(config)
    assert client.get(f"/api/v1/stocks/diagnoses/{did}/image").status_code == 200
    assert DIAG_FOOTER in rendered[0]


def test_diagnosis_image_no_image_config(env, rendered):
    client, _, config = env
    did = _add_diag(config)
    assert client.get(f"/api/v1/stocks/diagnoses/{did}/image").status_code == 200
    assert "贵州茅台" in rendered[0]


# ---------- 设置接口保留新字段 ----------

def test_settings_notifier_roundtrip_keeps_brand_fields(env, monkeypatch):
    from src import config_loader, settings_store

    client, _, config = env
    monkeypatch.setattr(config_loader, "reload_config", lambda: config)
    body = {"notifier": {"image": {"channels": ["wechat"], "kinds": ["daily_report"], "max_chars": 1500,
                                   "brand": "我的品牌", "footer": "我的页脚", "qr_url": "https://example.com/me"}}}
    assert client.put("/api/v1/settings/notifier", json=body).status_code == 200
    img = settings_store.read_settings()["notifier"]["image"]
    assert img["brand"] == "我的品牌" and img["footer"] == "我的页脚" and img["qr_url"] == "https://example.com/me"
    assert img["channels"] == ["wechat"]
