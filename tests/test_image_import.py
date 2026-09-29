"""图片识别导入自选股：服务、API、配置掩码。"""

from __future__ import annotations

import io

import pytest

from src.analyzers.llm_usage import FEATURE_LABELS
from src.services import image_import
from src.services.image_import import ALLOWED_TYPES, MAX_BYTES, extract_stocks
from tests.test_api import _wait, env  # noqa: F401  (env 是 fixture)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class FakeLLM:
    def __init__(self, reply="", exc=None):
        self.reply, self.exc, self.calls = reply, exc, []

    def chat_vision(self, prompt, images, system_message="", max_tokens=None):
        self.calls.append({"prompt": prompt, "images": images, "system": system_message})
        if self.exc:
            raise self.exc
        return self.reply


@pytest.fixture
def cfg(env):  # noqa: F811
    _, _, config = env
    return config


def _codes(result):
    return [c["code"] for c in result["candidates"]]


def test_constants_and_label():
    assert ALLOWED_TYPES == {"image/png", "image/jpeg", "image/webp", "image/gif"}
    assert MAX_BYTES == 5 * 1024 * 1024
    labels = {v for v in FEATURE_LABELS.values()}
    assert "图片识别" in labels
    assert FEATURE_LABELS.get("src.services.image_import") == "图片识别"


def test_validation_errors(cfg):
    llm = FakeLLM("[]")
    with pytest.raises(ValueError):
        extract_stocks(PNG, "application/pdf", cfg, llm=llm)
    with pytest.raises(ValueError):
        extract_stocks(b"", "image/png", cfg, llm=llm)
    with pytest.raises(ValueError):
        extract_stocks(b"0" * (MAX_BYTES + 1), "image/png", cfg, llm=llm)
    assert llm.calls == []


def test_code_fence_and_extra_text(cfg):
    reply = '好的，识别结果如下：\n```json\n{"stocks": [{"code": "600519", "name": "贵州茅台"}, {"code": "", "name": "中远海控"}]}\n```\n以上。'
    res = extract_stocks(PNG, "image/png", cfg, llm=FakeLLM(reply))
    assert sorted(_codes(res)) == ["600519", "601919"]
    assert all(c["name"] for c in res["candidates"])
    assert res["unresolved"] == []


def test_list_reply_and_extra_text_without_fence(cfg):
    reply = '结果：[{"code": "601919", "name": "中远海控"}] 完毕'
    res = extract_stocks(PNG, "image/png", cfg, llm=FakeLLM(reply))
    assert _codes(res) == ["601919"]


def test_dedupe_by_code(cfg):
    reply = '[{"code":"600519","name":"贵州茅台"},{"code":"600519","name":"茅台"},{"name":"贵州茅台"}]'
    res = extract_stocks(PNG, "image/png", cfg, llm=FakeLLM(reply))
    assert _codes(res).count("600519") == 1


def test_unresolved_and_empty(cfg):
    reply = '[{"code":"600519","name":"贵州茅台"},{"code":"","name":"不存在的股票XYZ"}]'
    res = extract_stocks(PNG, "image/png", cfg, llm=FakeLLM(reply))
    assert _codes(res) == ["600519"]
    assert len(res["unresolved"]) == 1 and "XYZ" in res["unresolved"][0]
    empty = extract_stocks(PNG, "image/png", cfg, llm=FakeLLM("[]"))
    assert empty["candidates"] == [] and empty["unresolved"] == []


def test_non_json_reply_does_not_crash(cfg):
    try:
        res = extract_stocks(PNG, "image/png", cfg, llm=FakeLLM("抱歉，我看不清这张图片"))
    except ValueError:
        return
    assert res["candidates"] == []


def test_name_with_spaces_and_fullwidth(cfg):
    reply = '[{"code":"","name":" 贵州 茅台 "},{"code":"６０１９１９","name":"中远海控"}]'
    try:
        res = extract_stocks(PNG, "image/png", cfg, llm=FakeLLM(reply))
    except ValueError:
        return
    assert isinstance(res["candidates"], list) and isinstance(res["unresolved"], list)
    codes = _codes(res)
    assert len(codes) == len(set(codes))


def test_image_sent_to_llm(cfg):
    llm = FakeLLM("[]")
    extract_stocks(PNG, "image/png", cfg, llm=llm)
    assert len(llm.calls) == 1
    assert llm.calls[0]["images"] == [(PNG, "image/png")]


# ---------- API ----------

def test_import_image_endpoint(env, monkeypatch):  # noqa: F811
    client, _, _ = env
    monkeypatch.setattr(image_import, "extract_stocks",
                        lambda image, mime, config, llm=None: {"candidates": [{"code": "600519", "name": "贵州茅台", "raw": "茅台"}], "unresolved": ["??"]})
    r = client.post("/api/v1/watchlist/import-image", files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 200
    done = _wait(client, r.json())
    assert done["status"] == "done"
    assert done["result"]["candidates"][0]["code"] == "600519" and done["result"]["unresolved"] == ["??"]


def test_import_image_rejects(env):  # noqa: F811
    client, _, _ = env
    r = client.post("/api/v1/watchlist/import-image", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 400
    big = io.BytesIO(b"0" * (MAX_BYTES + 1))
    r = client.post("/api/v1/watchlist/import-image", files={"file": ("a.png", big, "image/png")})
    assert r.status_code == 400


def test_vision_key_masked_and_preserved(env, monkeypatch):  # noqa: F811
    from src import config_loader
    from src import settings_store

    client, _, config = env
    config["llm"]["vision"] = {"provider": "qwen", "api_key": "sk-vision-9876", "model": "qwen-vl-max", "base_url": "https://dashscope/v1"}
    got = client.get("/api/v1/settings/llm").json()
    assert got["llm"]["vision"]["api_key"] == "******9876"
    monkeypatch.setattr(config_loader, "reload_config", lambda: config)
    body = {"llm": {"primary": {"provider": "deepseek", "api_key": "******1234", "model": "deepseek-chat"},
                    "vision": {"provider": "qwen", "api_key": "******9876", "model": "qwen-vl-plus", "base_url": "https://dashscope/v1"}}}
    assert client.put("/api/v1/settings/llm", json=body).json() == {"ok": True}
    written = settings_store.read_settings()["llm"]
    assert written["vision"]["api_key"] == "sk-vision-9876"
    assert written["vision"]["model"] == "qwen-vl-plus"
    assert written["primary"]["api_key"] == "sk-secret-1234"
