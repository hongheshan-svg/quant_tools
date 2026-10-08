"""八项修复与六类扩展的新增页面验收；临时数据库，阻断浏览器外网。"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/quant-alignment-browser')
BASE = 'http://127.0.0.1:8799'


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    log = (OUT / 'service.log').open('w')
    server = subprocess.Popen([sys.executable, str(ROOT / 'scripts/web_smoke_fixture.py'), '8799'], cwd=ROOT,
        env={**os.environ, 'QUANT_AUDIT_EXTENSIONS': '1', 'QUANT_NO_NOTIFY': '1'}, stdout=log, stderr=log)
    release = asyncio.Event()
    results, errors = {}, []
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context(viewport={'width': 1440, 'height': 1050})
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
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('dialog', lambda dialog: asyncio.create_task(dialog.accept()))

            await page.goto(BASE + '/settings?tab=llm')
            field = page.locator('input[list="models-主力模型"]')
            await expect(field).to_be_visible()
            await field.fill('audit-saved-model')
            saved, entered = asyncio.Event(), asyncio.Event()
            async def delayed_refresh(route):
                if route.request.method == 'PUT':
                    response = await route.fetch()
                    saved.set()
                    await route.fulfill(response=response)
                elif saved.is_set() and route.request.method == 'GET':
                    entered.set()
                    await release.wait()
                    await route.continue_()
                else:
                    await route.continue_()
            await page.route('**/api/v1/settings/llm', delayed_refresh)
            await page.get_by_role('button', name='保存', exact=True).click()
            await asyncio.wait_for(entered.wait(), 10)
            await expect(field).to_be_disabled()
            await page.screenshot(path=str(OUT / 'settings-refresh-protected.png'), full_page=True)
            await page.get_by_role('tab', name='备份与恢复').click()
            await expect(page.get_by_label('配置内容', exact=True)).to_be_disabled()
            release.set()
            await expect(page.get_by_role('button', name='导入', exact=True)).to_be_enabled()
            await page.get_by_role('tab', name='AI 模型').click()
            await expect(field).to_have_value('audit-saved-model')
            await field.fill('audit-next-model')
            await page.get_by_role('button', name='保存', exact=True).click()
            await expect(field).to_be_enabled()
            current = await context.request.get(BASE + '/api/v1/settings/llm')
            assert (await current.json())['llm']['primary']['model'] == 'audit-next-model'
            results['settings_refresh'] = {'slow_get_locks_fields_and_import': True, 'next_edit_preserved': 'audit-next-model'}
            await page.get_by_role('tab', name='AI 模型', exact=True).click()
            await page.get_by_label('AI 输出语言', exact=False).select_option('ko')
            await expect(page.get_by_label('AI 输出语言', exact=False)).to_be_enabled()
            assert (await (await context.request.get(BASE + '/api/v1/settings/report')).json())['language'] == 'ko'
            results['korean_report_setting'] = 'saved'

            await page.goto(BASE + '/integrations')
            await expect(page.get_by_text('72 项', exact=True)).to_be_visible()
            await page.get_by_label('宏观查询内容').select_option('health')
            await page.get_by_role('button', name='查询宏观数据').click()
            await expect(page.get_by_text('隔离验收宏观服务', exact=False)).to_be_visible()
            await page.get_by_label('本地模型问题').fill('检查自选股')
            await page.get_by_role('button', name='发送', exact=True).click()
            await expect(page.get_by_text('隔离验收本地回答，已恢复 0 条历史消息。', exact=False)).to_be_visible(timeout=20000)
            await page.get_by_label('本地模型问题').fill('继续核对质量')
            await page.get_by_role('button', name='发送', exact=True).click()
            await expect(page.get_by_text('隔离验收本地回答，已恢复 2 条历史消息。', exact=False)).to_be_visible(timeout=20000)
            await page.reload()
            await expect(page.get_by_label('继续已有会话').locator('option')).to_have_count(2)
            sid = await page.get_by_label('继续已有会话').locator('option').nth(1).get_attribute('value')
            await page.get_by_label('继续已有会话').select_option(sid)
            await expect(page.get_by_text('隔离验收本地回答，已恢复 2 条历史消息。', exact=False)).to_be_visible()
            await page.get_by_label('本地模型问题').fill('测试取消')
            await page.get_by_role('button', name='发送', exact=True).click()
            await expect(page.get_by_role('button', name='取消', exact=True)).to_be_enabled()
            await page.get_by_role('button', name='取消', exact=True).click()
            await expect(page.get_by_role('button', name='发送', exact=True)).to_be_enabled(timeout=20000)
            history = await (await context.request.get(BASE + '/api/v1/integrations/runtime/sessions/' + sid)).json()
            assert len(history['messages']) == 4 and history['last_run']['status'] == 'cancelled'
            await page.screenshot(path=str(OUT / 'integrations.png'), full_page=True)
            results['integrations'] = {'catalogue': 72, 'macro_query': True, 'session_reload_resume': True, 'cancel_without_history_pollution': True}

            await page.goto(BASE + '/overseas')
            await expect(page.get_by_text('AAPL · US', exact=True)).to_be_visible()
            await expect(page.get_by_text('已核对完整日线', exact=True)).to_be_visible()
            await page.get_by_role('button', name='更新财务与新闻', exact=True).click()
            await expect(page.get_by_text('隔离验收公司财报', exact=True)).to_be_visible(timeout=20000)
            await expect(page.get_by_text('1,000,000 USD', exact=True)).to_be_visible()
            results['overseas_evidence'] = {'financial_currency': 'USD', 'news_link_visible': True}
            await page.locator('summary', has_text='导入券商持仓 / 成交 CSV').click()
            await page.get_by_label('券商持仓 CSV').fill('symbol,quantity,average_cost,currency\nAAPL,10,180,USD')
            await page.get_by_role('button', name='检查导入', exact=True).click()
            await expect(page.get_by_role('button', name='确认导入 1 条')).to_be_enabled()
            assert not (await (await context.request.get(BASE + '/api/v1/overseas/portfolio')).json())['positions']
            await page.get_by_role('button', name='确认导入 1 条').click()
            await expect(page.get_by_role('cell', name='10', exact=True)).to_be_visible()
            initial = await (await context.request.get(BASE + '/api/v1/overseas/portfolio')).json()
            assert initial['market_value_cny'] is None
            await page.get_by_role('button', name='更新持仓行情与汇率', exact=True).click()
            await expect(page.get_by_role('button', name='更新持仓行情与汇率', exact=True)).to_be_enabled(timeout=20000)
            updated = await (await context.request.get(BASE + '/api/v1/overseas/portfolio')).json()
            assert updated['market_value_cny'] > 0
            await page.get_by_role('button', name='读取 Futu 持仓标的', exact=True).click()
            await expect(page.get_by_role('button', name='00700.HK', exact=True)).to_be_visible()
            await page.get_by_role('button', name='生成研究报告', exact=True).click()
            await expect(page.get_by_text('隔离验收研究：使用原币行情，缺少新闻与财务证据。', exact=False).first).to_be_visible(timeout=20000)
            await page.screenshot(path=str(OUT / 'overseas.png'), full_page=True)
            await page.goto(BASE + '/stocks/00700.HK')
            await expect(page.get_by_text('00700.HK · HK', exact=True)).to_be_visible()
            results['overseas'] = {'preview_does_not_write': True, 'confirmed_positions': 1, 'missing_fx_blocks_total': True,
                'converted_value_cny': updated['market_value_cny'], 'futu_symbols': True, 'diagnosis_rendered': True, 'stock_route': True}
            await page.goto(BASE + '/review')
            await page.get_by_label('复盘市场', exact=True).select_option('JP')
            await page.get_by_role('button', name='生成复盘', exact=True).click()
            await expect(page.get_by_role('heading', name='JP ·', exact=False).first).to_be_visible(timeout=20000)
            await expect(page.get_by_text('추세 구조', exact=True)).to_be_visible()
            assert 'region=JP' in await page.get_by_role('link', name='分享图', exact=True).get_attribute('href')
            await page.get_by_label('定时复盘-日股', exact=True).check()
            await page.get_by_label('定时复盘-韩股', exact=True).check()
            await page.get_by_role('button', name='保存复盘范围', exact=True).click()
            await expect(page.get_by_role('button', name='保存复盘范围', exact=True)).to_be_enabled()
            settings = await (await context.request.get(BASE + '/api/v1/settings/market-review')).json()
            assert set(settings['regions']) == {'CN', 'JP', 'KR'}
            await page.screenshot(path=str(OUT / 'regional-review.png'), full_page=True)
            await page.get_by_label('复盘市场', exact=True).select_option('KR')
            await expect(page.get_by_text('还没有复盘。', exact=False)).to_be_visible()
            await page.get_by_role('button', name='生成复盘', exact=True).click()
            await expect(page.get_by_text('KOSPI', exact=False)).to_be_visible(timeout=20000)
            results['regional_review'] = {'jp_and_kr_generated': True, 'korean_labels': True, 'separate_cache': True, 'settings': settings}
            await page.set_viewport_size({'width': 390, 'height': 844})
            await page.goto(BASE + '/overseas')
            await expect(page.get_by_text('AAPL · US', exact=True)).to_be_visible()
            await page.screenshot(path=str(OUT / 'overseas-mobile.png'), full_page=True)
            results['browser_errors'] = errors
            assert not errors, errors
            (OUT / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
            print(json.dumps(results, ensure_ascii=False))
            await browser.close()
    finally:
        release.set()
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()


if __name__ == '__main__':
    asyncio.run(main())
