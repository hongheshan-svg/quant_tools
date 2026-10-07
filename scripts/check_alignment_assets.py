"""验证默认策略、AI模板和部署启动；可在源码、Docker与安装包验收中复用。"""

from pathlib import Path
import argparse
import json
import time
import urllib.request
import sys
import os
import re

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check_assets():
    from src.services.strategy_skills import load_skills
    from src.strategy.screening_rules import load_rules
    from src.strategy.screening_pipeline import load_profiles
    from src.config_loader import load_config
    assert load_rules("config/screening_rules.yaml"), "默认规则缺失"
    assert len(load_profiles("config/scoring_profiles.yaml")) == 10, "默认评分策略缺失"
    assert load_skills(), "AI 策略资源缺失"
    assert all(s.instructions_en for s in load_skills() if s.source == "builtin"), "内置策略英文规则缺失"
    cfg = load_config("config/settings.yaml.example")
    assert cfg["screening"]["profiles_file"] and cfg["screening"]["pipeline"]["enabled"]
    assert cfg['etf_rotation']['risk_assets'] and cfg['etf_rotation']['lookback_days'] > 0
    assert (ROOT / 'docs/licenses/daily_stock_analysis-MIT.txt').is_file(), 'ETF 规则引擎许可证未打包'
    print("默认筛选、评分与AI策略资源验证通过")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url")
    parser.add_argument("--wait", type=int, default=0)
    args = parser.parse_args()
    if not args.url:
        check_assets()
        return
    expires = time.monotonic() + args.wait
    while True:
        try:
            with urllib.request.urlopen(args.url + "/api/v1/health", timeout=3) as response:
                assert response.status == 200
            break
        except (OSError, TimeoutError):
            if time.monotonic() >= expires:
                raise
            time.sleep(1)
    for path in ('/', '/api/v1/chat/skills', '/api/v1/settings/data-sources', '/api/v1/settings/screening', '/api/v1/screening/rotation/settings', '/api/v1/system/data-center', '/api/v1/alerts/rules'):
        token = os.environ.get("QUANT_SMOKE_API_TOKEN")
        headers = {"Authorization": "Bearer " + token} if token else {}
        with urllib.request.urlopen(urllib.request.Request(args.url + path, headers=headers), timeout=5) as response:
            payload = response.read()
            assert response.status == 200 and payload, path
            if path == "/api/v1/chat/skills":
                assert json.loads(payload), "AI 策略未打包"
            if path == "/api/v1/settings/screening":
                assert len(json.loads(payload)["profiles"]) == 10, "默认评分策略未打包"
            if path == '/api/v1/screening/rotation/settings':
                assert json.loads(payload)['risk_assets'], 'ETF 轮动默认候选池未打包'
            if path == "/":
                asset = re.search(r'src="(/assets/[^\"]+\.js)"', payload.decode())
                assert asset, "Web 入口资源缺失"
                with urllib.request.urlopen(urllib.request.Request(args.url + asset[1], headers=headers), timeout=5) as javascript:
                    assert javascript.status == 200 and "javascript" in javascript.headers.get("Content-Type", "") and javascript.read(), "Web 构建资源不可用"
    request = urllib.request.Request(args.url + '/api/v1/screening/snapshot/check',
                                     data=json.dumps({'snapshot': {'close': 10, 'amount': 200000000, 'change_pct': 4}}).encode(),
                                     headers={**headers, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        snapshot = json.load(response)
        assert response.status == 200 and snapshot['network_used'] is False and snapshot['strategies'], '快照诊断不可用'
    print("部署接口、Web、快照诊断和默认AI策略启动验证通过")


if __name__ == "__main__":
    main()
