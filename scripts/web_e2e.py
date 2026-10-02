"""构建产物的真实浏览器回归；启动隔离服务，关闭外网与真实业务写入。"""

from pathlib import Path
import argparse
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--output", default="/tmp/quant-web-e2e")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"
    log = (output / "service.log").open("w")
    service = subprocess.Popen([sys.executable, str(ROOT / "scripts/web_smoke_fixture.py"), str(args.port)], cwd=ROOT, stdout=log, stderr=log)
    try:
        for _ in range(100):
            if service.poll() is not None:
                raise RuntimeError("隔离服务启动失败，请查看 service.log")
            try:
                urllib.request.urlopen(base + "/api/v1/auth/status", timeout=.5).close()
                break
            except (OSError, TimeoutError):
                time.sleep(.2)
        else:
            raise RuntimeError("隔离服务启动超时")
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base) or route.request.url.startswith("data:") else route.abort())
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            expect(page.get_by_role("heading", name="研究工作台")).to_be_visible()
            page.screenshot(path=str(output / "workspace.png"), full_page=True)
            # 同一标的两份相反结论：选中旧报告后研究、导出都绑定旧身份。
            old_summary = "趋势仍在，等待量能和最新资金数据确认。"
            page.get_by_role("tab", name="近期报告").click()
            page.get_by_role("button", name=old_summary, exact=False).click()
            page.get_by_role("tab", name="研究概览").click()
            expect(page.get_by_text("2026-10-02 08:00 · #1", exact=False)).to_be_visible()
            expect(page.get_by_text(old_summary, exact=True).last).to_be_visible()
            exported = context.request.get(base + "/api/v1/stocks/diagnoses/1/markdown")
            assert exported.ok and old_summary in exported.text() and "新证据出现后回避" not in exported.text()
            page.screenshot(path=str(output / "historical-report.png"), full_page=True)
            page.goto(base + "/screening")
            expect(page.get_by_role("heading", name="策略选股")).to_be_visible()
            expect(page.get_by_label("风险筛选")).to_be_visible()
            expect(page.get_by_text("数据不完整", exact=False).first).to_be_visible()
            page.get_by_text("评分明细", exact=True).click()
            expect(page.get_by_text("估值：缺失", exact=True)).to_be_visible()
            page.screenshot(path=str(output / "screening.png"), full_page=True)
            page.get_by_role("button", name="研究报告", exact=True).click()
            expect(page.get_by_role("dialog")).to_be_visible()
            expect(page.get_by_role("dialog").get_by_text(old_summary, exact=True)).to_be_visible()
            page.goto(base + "/alerts")
            expect(page.get_by_label("级别筛选")).to_be_visible()
            expect(page.get_by_label("渠道筛选")).to_be_visible()
            page.goto(base + "/sources")
            expect(page.get_by_role("heading", name="数据源状态")).to_be_visible()
            expect(page.get_by_text("模拟连接超时；保留后备源", exact=False)).to_be_visible()
            page.screenshot(path=str(output / "sources.png"), full_page=True)
            page.goto(base + "/settings")
            page.get_by_role("tab", name="选股策略").click()
            expect(page.get_by_text("多因子策略权重", exact=True)).to_be_visible()
            page.locator("details").first.locator("summary").click()
            page.get_by_label("动量", exact=True).first.fill("0.7")
            with page.expect_response(lambda r: r.url.endswith("/api/v1/settings/screening") and r.request.method == "PUT") as saving:
                page.get_by_role("button", name="保存", exact=True).click()
            assert saving.value.ok, saving.value.text()
            settings = context.request.get(base + "/api/v1/settings/screening").json()
            assert settings["profiles"][0]["weights"]["momentum"] == .7
            page.screenshot(path=str(output / "strategy-settings.png"), full_page=True)
            # 部分采集必须是 error 终态，保留已取得的证据；刷新仍能恢复。
            task = context.request.post(base + "/api/v1/pipeline/collect").json()
            for _ in range(100):
                state = context.request.get(base + "/api/v1/tasks/" + task["id"]).json()
                if state["status"] in {"error", "done"}:
                    break
                time.sleep(.02)
            assert state["status"] == "error" and state["result"]["news"]["cailianshe"] == 3
            events = context.request.get(base + "/api/v1/tasks/" + task["id"] + "/events").text()
            assert '"status":"error"' in events.replace(" ", "")
            page.goto(base)
            page.reload()
            expect(page.get_by_text("数据采集不完整：market.fund_flow", exact=False).first).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.get_by_role("heading", name="研究工作台")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / "workspace-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.goto(base + "/sources")
            # 身份接口错误要停留在可重试状态，恢复后正常加载。
            context.route("**/api/v1/auth/status", lambda route: route.fulfill(status=503, json={"detail": "隔离故障"}))
            page.reload()
            expect(page.get_by_text("连接服务失败", exact=False)).to_be_visible()
            context.unroute("**/api/v1/auth/status")
            page.get_by_role("button", name="重试").click()
            expect(page.get_by_role("heading", name="数据源状态")).to_be_visible()
            assert not errors, errors
            browser.close()
        print("浏览器验收通过：工作台、历史研究与导出绑定、选股筛选、评分设置保存、提醒审计、来源降级、任务失败与刷新恢复、窄屏、身份错误恢复；无页面异常。")
    finally:
        service.terminate()
        try:
            service.wait(timeout=10)
        except subprocess.TimeoutExpired:
            service.kill()
            service.wait()
        log.close()


if __name__ == "__main__":
    main()
