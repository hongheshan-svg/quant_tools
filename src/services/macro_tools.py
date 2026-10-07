"""固定客户端目录的 FXMacroData 原生工具；凭据只在主机端注入。"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
import os


@lru_cache(maxsize=1)
def operations() -> tuple:
    try:
        from fxmacrodata_public import list_operations
        return tuple(list_operations())
    except ImportError:
        return ()


def catalogue() -> list[dict]:
    return [{"name": o.name, "description": o.description, "parameters": deepcopy(o.input_schema)}
            for o in operations()]


def schemas() -> list[dict]:
    return [{"type": "function", "function": {"name": "fxmacrodata_" + o.name,
             "description": o.description, "parameters": deepcopy(o.input_schema)}} for o in operations()]


def execute(config: dict, name: str, arguments: dict) -> dict:
    operation = next((o for o in operations() if o.name == name), None)
    if operation is None:
        return {"status": "unavailable", "reason": "unknown_operation"}
    # 参数只能来自固定公开目录，不允许模型覆盖 URL 或主机凭据。
    from jsonschema import validate, ValidationError
    try:
        validate(arguments, operation.input_schema)
    except ValidationError:
        return {"status": "invalid", "reason": "invalid_arguments"}
    client = None
    try:
        from fxmacrodata_public import FXMacroDataClient
        cfg = (config.get("integrations") or {}).get("fxmacrodata") or {}
        key = cfg.get("api_key") or os.environ.get("FXMACRODATA_API_KEY")
        client = FXMacroDataClient(api_key=key or None, timeout=30)
        result = client.execute(name, arguments).as_dict()
        # 正常结果保留原始来源、发布时间、预测类型；异常不向模型回显请求头。
        if key:
            result = json.loads(json.dumps(result, ensure_ascii=False).replace(str(key), "[已隐藏]"))
        return result
    except Exception:
        return {"status": "unavailable", "reason": "provider_unavailable", "operation": name}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
