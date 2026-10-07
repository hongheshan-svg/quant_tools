"""扩展接入配置的字段目录；界面、校验和脱敏使用同一份定义。"""
from copy import deepcopy

FIELDS = {
    "fxmacrodata": {"label": "宏观数据", "fields": {"api_key": ("授权密钥（公开数据可留空）", "", True)}},
    "adanos": {"label": "美股社交情报", "fields": {"api_key": ("Adanos API Key", "", True)}},
    "longbridge": {"label": "Longbridge 港美行情", "fields": {
        "app_key": ("App Key", "", True), "app_secret": ("App Secret", "", True),
        "access_token": ("Access Token", "", True)}},
    "futu": {"label": "Futu 持仓标的（只读）", "fields": {
        "host": ("OpenD 主机", "127.0.0.1", False), "port": ("OpenD 端口", 11111, False)}},
    "runtime": {"label": "本地模型运行", "fields": {
        "timeout_seconds": ("超时（秒）", 300, False),
        "codex_cli": ("Codex 可执行文件", "codex", False),
        "claude_code_cli": ("Claude Code 可执行文件", "claude", False),
        "opencode_cli": ("OpenCode 可执行文件", "opencode", False),
        "hermes_url": ("Hermes 本机地址", "http://127.0.0.1:8642/v1", False)}},
}


def settings(config: dict, *, masked: bool = True) -> dict:
    value = deepcopy(config.get("integrations") or {})
    for group, spec in FIELDS.items():
        section = value.setdefault(group, {})
        for key, (_, default, secret) in spec["fields"].items():
            section.setdefault(key, default)
            if masked and secret and section[key]:
                section[key] = "******"
    return value


def metadata() -> list[dict]:
    return [{"key": group, "label": spec["label"], "fields": [
        {"key": key, "label": label, "secret": secret, "type": "number" if isinstance(default, int) else "text"}
        for key, (label, default, secret) in spec["fields"].items()]} for group, spec in FIELDS.items()]


def merge_settings(config: dict, body: dict) -> dict:
    value = settings(config, masked=False)
    for group, section in body.items():
        if group not in FIELDS or not isinstance(section, dict):
            raise ValueError("未知接入配置")
        for key, item in section.items():
            if key not in FIELDS[group]["fields"]:
                raise ValueError("未知接入字段")
            _, default, secret = FIELDS[group]["fields"][key]
            if secret and item == "******":
                continue
            if isinstance(default, int):
                if type(item) is not int or not 1 <= item <= (3600 if key == "timeout_seconds" else 65535):
                    raise ValueError("端口或超时超出范围")
            elif not isinstance(item, str) or len(item) > 4096 or "\x00" in item:
                raise ValueError("接入字段无效")
            value[group][key] = item
    from src.analyzers.local_runtime import validate_hermes_url
    validate_hermes_url(value["runtime"]["hermes_url"])
    return value
