"""14 类修复的 Chromium 验收；只操作临时配置与合成行情，阻断浏览器外网。"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import yaml
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/quant-dsa-fixes-browser')
BASE = 'http://127.0.0.1:8798'


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    log = (OUT / 'service.log').open('w')
    server = subprocess.Popen([sys.executable, str(ROOT / 'scripts/web_smoke_fixture.py'), '8798'], cwd=ROOT,
        env={**os.environ, 'QUANT_AUDIT_FIXES': '1', 'QUANT_NO_NOTIFY': '1'}, stdout=log, stderr=log)
    release = asyncio.Event()
    browser = None
    results = {}
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context(viewport={'width': 1400, 'height': 1000}, permissions=['clipboard-read', 'clipboard-write'])
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError('隔离服务退出，检查 service.log')
                try:
                    response = await context.request.get(BASE + '/api/v1/auth/status', timeout=500)
                    if response.ok:
                        break
                except Exception:
                    await asyncio.sleep(.2)
            else:
                raise RuntimeError('服务启动超时')
            await context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(BASE) else route.abort())
            page = await context.new_page()
            errors, writes = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: writes.append(request.url) if request.method in ('PUT', 'POST', 'DELETE') else None)
            page.on('dialog', lambda dialog: asyncio.create_task(dialog.accept()))
            await page.goto(BASE + '/settings?tab=screening')
            field = page.get_by_label('财务候选数', exact=True)
            await expect(field).to_be_visible()
            await field.fill('11')
            entered = asyncio.Event()
            async def delayed(route):
                if route.request.method == 'PUT':
                    entered.set()
                    await release.wait()
                await route.continue_()
            await page.route('**/api/v1/settings/screening', delayed)
            await page.get_by_role('button', name='保存', exact=True).click()
            await asyncio.wait_for(entered.wait(), 10)
            await expect(field).to_be_disabled()
            await page.get_by_text('正在保存设置，请等待完成后继续编辑或导入', exact=True).scroll_into_view_if_needed()
            await page.screenshot(path=str(OUT / 'settings-pending.png'), full_page=True)
            await page.get_by_role('tab', name='备份与恢复').click()
            await expect(page.get_by_label('配置内容', exact=True)).to_be_disabled()
            await expect(page.get_by_role('button', name='导入', exact=True)).to_be_disabled()
            assert not any(url.endswith('/settings/import') for url in writes)
            release.set()
            await expect(page.get_by_role('button', name='导入', exact=True)).to_be_enabled()
            exported = await context.request.get(BASE + '/api/v1/settings/export')
            config = yaml.safe_load(await exported.text())
            assert config['screening']['pipeline']['financial_candidates'] == 11
            config['screening']['pipeline']['financial_candidates'] = 29
            await page.get_by_label('配置内容', exact=True).fill(yaml.safe_dump(config, allow_unicode=True))
            async with page.expect_response(lambda r: r.url.endswith('/settings/import') and r.request.method == 'POST') as imported:
                await page.get_by_role('button', name='导入', exact=True).click()
            assert (await imported.value).ok
            await page.get_by_role('tab', name='选股策略').click()
            await expect(field).to_have_value('29')
            current = await context.request.get(BASE + '/api/v1/settings/screening')
            assert (await current.json())['screening']['pipeline']['financial_candidates'] == 29
            results['settings'] = {'pending_input_disabled': True, 'concurrent_import_blocked': True, 'final_value': 29}

            await page.goto(BASE + '/sources')
            await page.get_by_role('tab', name='能力总览').click()
            await expect(page.get_by_text('模拟第二来源', exact=False).first).to_be_visible()
            await expect(page.get_by_text('健康检查时间', exact=False).first).to_be_visible()
            await page.screenshot(path=str(OUT / 'sources.png'), full_page=True)
            results['source_batch'] = 'mixed sources visible, health timestamps separate'

            await page.goto(BASE + '/screening')
            await expect(page.get_by_text('选股数据源运行历史', exact=True)).to_be_visible()
            await page.locator('details').filter(has=page.locator('summary', has_text='2026-09-30 · 数据不完整')).first.locator('summary').click()
            await expect(page.get_by_text('季度基本面 · 模拟后备', exact=False)).to_be_visible()
            await page.get_by_label('风险 ETF 池', exact=True).fill('510300')
            await page.get_by_label('防守 ETF（留空为现金）', exact=True).fill('511880')
            await page.get_by_label('开始日期', exact=True).fill('2026-09-01')
            await page.get_by_label('截止日期（留空为已收盘日）', exact=True).fill('2026-09-18')
            await page.get_by_label('动量交易日数', exact=True).fill('2')
            await page.get_by_role('button', name='运行 ETF 轮动回测', exact=True).click()
            await expect(page.get_by_text('有效回测不足一年', exact=False)).to_be_visible(timeout=20000)
            await expect(page.get_by_text('CASH 100.0%', exact=False).first).to_be_visible()
            await expect(page.get_by_text('年度收益与参数稳定性', exact=True)).to_have_count(0)
            await page.get_by_text('目标仅供研究，成交须等待执行日有效报价', exact=False).scroll_into_view_if_needed()
            await page.screenshot(path=str(OUT / 'etf-short-history.png'), full_page=True)
            results['etf'] = {'short_history_metrics_hidden': True, 'defensive_missing_cash': True}

            await page.goto(BASE + '/history?id=1')
            await page.get_by_role('dialog').get_by_text('运行记录', exact=True).click()
            await expect(page.get_by_text('报告保存', exact=True)).to_be_visible()
            await page.get_by_role('button', name='复制排障摘要', exact=True).click()
            copied = await page.evaluate('navigator.clipboard.readText()')
            assert '报告保存' in copied and 'isolated-fix-acceptance' in copied
            await page.get_by_text('报告保存', exact=True).scroll_into_view_if_needed()
            await page.screenshot(path=str(OUT / 'run-diagnostics.png'), full_page=True)
            results['diagnostics'] = {'save_step_persisted': True, 'clipboard_summary': True}
            await page.keyboard.press('Escape')
            await page.goto(BASE + '/')
            await page.get_by_role('button', name='任务中心', exact=True).click()
            task = page.locator('div.rounded').filter(has=page.locator('span', has_text='隔离运行流验收')).filter(has=page.locator('summary', has_text='运行详情')).last
            await task.get_by_text('运行详情', exact=True).click()
            await expect(page.get_by_text('模拟失败步骤', exact=True)).to_be_visible()
            results['failed_task_flow'] = True
            results['page_errors'] = errors
            assert not errors, errors
            (OUT / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        release.set()
        if browser:
            await browser.close()
        server.terminate()
        try:
            server.wait(timeout=8)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        log.close()


if __name__ == '__main__':
    asyncio.run(main())
