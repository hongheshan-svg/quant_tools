"""隔离浏览器审计：复现 LLM 保存后的慢刷新覆盖新草稿，不调用外部模型。

从仓库根目录执行：.venv/bin/python docs/audits/dsa-v1.2.0-review-2026-10-07/browser_probe.py
此探针记录 v1.2.0 的问题行为，修复后应更新为验证新草稿得到保护。
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[3]
OUT = Path("/tmp/quant-v120-browser-audit")
BASE = "http://127.0.0.1:8806"


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "service.log").open("w") as log:
        server = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts/web_smoke_fixture.py"), "8806"],
            cwd=ROOT, env={**os.environ, "QUANT_NO_NOTIFY": "1"}, stdout=log, stderr=log,
        )
        release = asyncio.Event()
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                context = await browser.new_context(viewport={"width": 1400, "height": 1000})
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError("隔离服务退出，检查 service.log")
                    try:
                        response = await context.request.get(BASE + "/api/v1/auth/status", timeout=500)
                        if response.ok:
                            break
                    except Exception:
                        await asyncio.sleep(.2)
                else:
                    raise RuntimeError("隔离服务启动超时")
                await context.route("**/*", lambda route: route.continue_()
                    if route.request.url.startswith(BASE + "/") else route.abort())
                page = await context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(BASE + "/settings?tab=llm")
                field = page.locator('input[list="models-主力模型"]')
                await expect(field).to_be_visible()
                await field.fill("audit-saved-model")
                entered = asyncio.Event()

                async def delayed_refresh(route):
                    if route.request.method == "GET":
                        response = await route.fetch()
                        entered.set()
                        await release.wait()
                        await route.fulfill(response=response)
                    else:
                        await route.continue_()

                await page.route("**/api/v1/settings/llm", delayed_refresh)
                await page.get_by_role("button", name="保存", exact=True).click()
                await asyncio.wait_for(entered.wait(), 10)
                await expect(field).to_be_enabled()
                await field.fill("audit-unsaved-model")
                before = await field.input_value()
                await page.screenshot(path=str(OUT / "settings-refresh-before.png"), full_page=True)
                release.set()
                await expect(field).to_have_value("audit-saved-model")
                after = await field.input_value()
                await page.screenshot(path=str(OUT / "settings-refresh-after.png"), full_page=True)
                results = {"settings_refresh_race": {
                    "input_enabled_while_refresh_pending": True,
                    "draft_before_response": before, "draft_after_response": after,
                    "unsaved_edit_lost": before != after,
                }, "page_errors": errors}
                assert not errors, errors
                (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
                print(json.dumps(results, ensure_ascii=False, indent=2))
                await browser.close()
        finally:
            release.set()
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())
