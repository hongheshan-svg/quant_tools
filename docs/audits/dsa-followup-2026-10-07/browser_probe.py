"""真实浏览器复现设置保存/导入并发；隔离配置、禁用外网。"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path
import yaml
from playwright.async_api import async_playwright, expect

ROOT = Path.cwd()
OUT = Path('/tmp/quant-dsa-followup-browser')
OUT.mkdir(exist_ok=True)
BASE = 'http://127.0.0.1:8796'

async def main():
    log = (OUT / 'service.log').open('w')
    server = subprocess.Popen([sys.executable, str(ROOT / 'scripts/web_smoke_fixture.py'), '8796'], cwd=ROOT, stdout=log, stderr=log)
    browser = None
    results = {}
    release = asyncio.Event()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context(viewport={'width': 1360, 'height': 980})
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError('隔离服务退出')
                try:
                    response = await context.request.get(BASE + '/api/v1/auth/status', timeout=500)
                    if response.ok:
                        break
                except Exception:
                    await asyncio.sleep(.2)
            else:
                raise RuntimeError('隔离服务启动超时')
            async def local_only(route):
                if route.request.url.startswith(BASE):
                    await route.continue_()
                else:
                    await route.abort()
            await context.route('**/*', local_only)
            page = await context.new_page()
            errors = []
            page.on('pageerror', lambda err: errors.append(str(err)))
            await page.goto(BASE + '/settings?tab=screening')
            await expect(page.get_by_label('财务候选数', exact=True)).to_be_visible()
            field = page.get_by_label('财务候选数', exact=True)
            await field.fill('11')
            entered = asyncio.Event()
            async def delayed_save(route):
                if route.request.method == 'PUT':
                    entered.set()
                    await release.wait()
                await route.continue_()
            await page.route('**/api/v1/settings/screening', delayed_save)
            await page.get_by_role('button', name='保存', exact=True).click()
            await asyncio.wait_for(entered.wait(), timeout=10)
            editable = await field.is_enabled()
            await field.fill('19')
            results['edit_during_save'] = {'submitted_value': '11', 'edit_while_pending': '19', 'input_enabled_while_saving': editable, 'before_response': await field.input_value()}
            await page.get_by_role('heading', name='设置', exact=True).scroll_into_view_if_needed()
            await page.screenshot(path=str(OUT / 'settings-before-response.png'), full_page=True)
            release.set()
            await expect(page.get_by_text('选股设置已保存', exact=True)).to_be_visible()
            await expect(field).to_have_value('11')
            results['edit_during_save']['after_response'] = await field.input_value()
            results['edit_during_save']['unsaved_status_count'] = await page.get_by_text('有未保存的设置', exact=True).count()
            await page.get_by_role('heading', name='设置', exact=True).scroll_into_view_if_needed()
            await page.screenshot(path=str(OUT / 'settings-after-response.png'), full_page=True)
            await page.unroute('**/api/v1/settings/screening', delayed_save)

            # 保存尚未完成时整体导入：先让导入完成，再让旧保存返回。
            # 两者都写入临时配置；旧请求覆盖导入值即为实际竞争结果。
            await page.reload()
            await expect(field).to_be_visible()
            entered = asyncio.Event()
            release.clear()
            await field.fill('13')
            await page.route('**/api/v1/settings/screening', delayed_save)
            await page.get_by_role('button', name='保存', exact=True).click()
            await asyncio.wait_for(entered.wait(), timeout=10)
            await page.get_by_role('tab', name='备份与恢复').click()
            export = await context.request.get(BASE + '/api/v1/settings/export')
            config = yaml.safe_load(await export.text())
            config['screening']['pipeline']['financial_candidates'] = 29
            await page.get_by_label('配置内容', exact=True).fill(yaml.safe_dump(config, allow_unicode=True))
            page.on('dialog', lambda dialog: asyncio.create_task(dialog.accept()))
            async with page.expect_response(lambda r: r.url.endswith('/api/v1/settings/import') and r.request.method == 'POST') as importing:
                await page.get_by_role('button', name='导入', exact=True).click()
            import_response = await importing.value
            assert import_response.ok, await import_response.text()
            current = await context.request.get(BASE + '/api/v1/settings/screening')
            results['import_during_save'] = {'pending_save_value': 13, 'imported_value': 29, 'import_finished_while_save_pending': not release.is_set(), 'server_value_after_import': (await current.json())['screening']['pipeline']['financial_candidates']}
            await page.screenshot(path=str(OUT / 'settings-import-pending-save.png'), full_page=True)
            async with page.expect_response(lambda r: r.url.endswith('/api/v1/settings/screening') and r.request.method == 'PUT'):
                release.set()
            current = await context.request.get(BASE + '/api/v1/settings/screening')
            results['import_during_save']['server_value_after_old_save'] = (await current.json())['screening']['pipeline']['financial_candidates']
            results['page_errors'] = errors
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

asyncio.run(main())
