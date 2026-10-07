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
            expect(page.get_by_text('ETF 双动量轮动', exact=True)).to_be_visible()
            page.get_by_role('button', name='检查条件', exact=True).click()
            expect(page.get_by_text('放量突破 ·', exact=False).first).to_be_visible()
            page.get_by_role('button', name='运行 ETF 轮动回测').click()
            expect(page.get_by_text('没有可用前复权 ETF 历史', exact=False).first).to_be_visible()
            page.screenshot(path=str(output / "screening.png"), full_page=True)
            page.get_by_role("button", name="研究报告", exact=True).click()
            expect(page.get_by_role("dialog")).to_be_visible()
            expect(page.get_by_role("dialog").get_by_text(old_summary, exact=True)).to_be_visible()
            page.goto(base + "/alerts")
            expect(page.get_by_label("级别筛选")).to_be_visible()
            expect(page.get_by_label("渠道筛选")).to_be_visible()
            page.get_by_label('级别筛选').select_option('warning')
            page.get_by_role('tab', name='提醒规则').click()
            expect(page.get_by_text('规则来源', exact=False)).to_be_visible()
            page.get_by_role('button', name='新增规则').click()
            page.get_by_label('规则类型').select_option('account_drawdown')
            expect(page.get_by_label('作用范围')).to_have_value('portfolio_account')
            page.get_by_label('账户范围').fill('real:家人')
            page.get_by_role('button', name='确定', exact=True).click()
            page.get_by_role('button', name='保存', exact=True).click()
            expect(page.get_by_text('账户风险 real:家人', exact=False)).to_be_visible()
            expect(page.get_by_text('生效 1 / 1', exact=False)).to_be_visible()
            expect(page.get_by_text('有未保存的修改，点「保存」后才会生效', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='保存', exact=True)).to_be_disabled()
            page.screenshot(path=str(output / 'alert-scopes.png'), full_page=True)
            page.get_by_role('tab', name='提醒记录').click()
            expect(page.get_by_label('级别筛选')).to_have_value('warning')
            page.screenshot(path=str(output / 'alert-filters.png'), full_page=True)
            page.goto(base + "/sources")
            expect(page.get_by_role("heading", name="数据源状态")).to_be_visible()
            expect(page.get_by_text("模拟连接超时；保留后备源", exact=False)).to_be_visible()
            page.screenshot(path=str(output / "sources.png"), full_page=True)
            page.get_by_role('tab', name='能力总览').click()
            expect(page.get_by_text('本地数据质量', exact=True)).to_be_visible()
            expect(page.get_by_text('供应商 × 数据集 × 场景', exact=True)).to_be_visible()
            expect(page.get_by_text('优先级 / 配置来源', exact=True)).to_be_visible()
            page.screenshot(path=str(output / 'data-center.png'), full_page=True)
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
            # 切换设置页签保留草稿；导航取消与保存失败都不能清空编辑值。
            page.get_by_label('动量', exact=True).first.fill('0.63')
            page.get_by_role('tab', name='AI 模型').click()
            page.get_by_role('tab', name='选股策略').click()
            expect(page.get_by_label('动量', exact=True).first).to_have_value('0.63')
            page.once('dialog', lambda dialog: dialog.dismiss())
            page.get_by_role('link', name='盘中提醒', exact=True).click()
            expect(page.get_by_role('heading', name='设置', exact=True)).to_be_visible()
            context.route('**/api/v1/settings/screening', lambda route: route.fulfill(status=500, json={'detail': '模拟保存失败'}) if route.request.method == 'PUT' else route.continue_())
            page.get_by_role('button', name='保存', exact=True).click()
            expect(page.get_by_text('模拟保存失败', exact=True)).to_be_visible()
            expect(page.get_by_label('动量', exact=True).first).to_have_value('0.63')
            page.screenshot(path=str(output / 'settings-draft.png'), full_page=True)
            context.unroute('**/api/v1/settings/screening')
            page.get_by_role('button', name='保存', exact=True).click()
            expect(page.get_by_text('有未保存的设置', exact=True)).to_have_count(0)
            page.goto(base + '/watchlist')
            expect(page.get_by_text('历史报告待更新', exact=True)).to_be_visible()
            expect(page.get_by_text('尚无报告', exact=True)).to_be_visible()
            page.screenshot(path=str(output / 'watchlist-status.png'), full_page=True)
            page.goto(base + '/chat')
            question = page.get_by_placeholder('输入问题，Enter 发送，Shift+Enter 换行')
            question.fill('分析平安，然后复盘大盘')
            question.press('Enter')
            expect(page.get_by_text('请确认「平安」', exact=False)).to_be_visible()
            page.get_by_role('button', name='平安银行 (000001)', exact=True).click()
            expect(page.get_by_text('隔离问股回答', exact=False).last).to_be_visible()
            page.reload()
            expect(page.get_by_text('执行阶段与耗时', exact=True).last).to_be_visible()
            page.locator('summary').filter(has_text='执行阶段与耗时').last.click()
            expect(page.get_by_text('模型思考', exact=False).first).to_be_visible()
            page.screenshot(path=str(output / 'chat-intent-stages.png'), full_page=True)
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
            # 全部主页面均能打开；没有数据和错误状态也必须正常渲染。
            for route in ('market', 'news', 'intelligence', 'watchlist', 'trading', 'real', 'review', 'themes', 'research', 'screening', 'performance', 'history', 'signals', 'alerts', 'sources', 'usage', 'settings', 'setup', 'stocks/600519'):
                page.goto(base + '/' + route)
                expect(page.locator('main h1').first).to_be_visible()
                expect(page.get_by_role('heading', name='页面出错', exact=True)).to_have_count(0)
            assert not errors, errors
            browser.close()
        print('浏览器验收通过：全部主页面、历史报告绑定、ETF 缺数据与快照诊断、设置草稿与失败保存、告警账户范围与筛选保留、自选状态、问股确认与阶段回放、任务恢复、窄屏与身份错误；无页面异常。')
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
