"""能力与运行质量分离；矩阵只读，不探测、不输出凭证、不夸大补充源范围。"""

import json
from src.services.data_capabilities import data_center
from tests.test_real_portfolio import config  # noqa: F401


def test_readonly_precise_provider_scope(config, monkeypatch):
    monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("能力页不能联网")))
    monkeypatch.setenv("QUANT__DATA_SOURCES__MIAOXIANG_API_KEY", "secret")
    config["data_sources"] = {"miaoxiang_api_key": "secret"}
    result = data_center(config)
    miao = [row for row in result["matrix"] if row["provider"] == "miaoxiang"]
    assert len(miao) == 2 and {row["dataset"] for row in miao} == {"筹码分布", "个股资金流补充"}
    assert all(row["configuration_origin"] == "environment_override" and row["asset_kinds"] == ["stock"] for row in miao)
    assert all(row["observation_timestamp"] is None for row in result["matrix"])
    assert "secret" not in json.dumps(result)
    assert result["read_only"] and len(result["snapshots"]) == 3
