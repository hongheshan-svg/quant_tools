import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { Diagnosis, HoldingsImportResult, OverseasSnapshot, OverseasPortfolio } from '@/api/types'
import { Badge, Button, Card, ErrorBox, Field, Input, PageHeader, Spinner } from '@/components/ui'
import { CandlestickChart, toCandles } from '@/components/CandlestickChart'
import { DiagnosisView } from '@/components/DiagnosisView'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'
import { toast } from '@/stores/toast'

const fmt = (v: number | null | undefined) => v == null ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: 2 })
export function OverseasPage({ initialCode = 'AAPL' }: { initialCode?: string }) {
  const [input, setInput] = useState(initialCode)
  const [code, setCode] = useState(initialCode)
  const snapshot = useApi(() => api.overseasSnapshot(code), [code])
  const task = useTask<OverseasSnapshot>()
  const diagnosisTask = useTask<Diagnosis>()
  const [report, setReport] = useState<Diagnosis | null>(null)
  const [social, setSocial] = useState<Record<string, unknown> | null>(null)
  const value = snapshot.data
  async function refresh() {
    try {
      const result = await task.run(() => api.refreshOverseas(code))
      if (result?.refresh_status === 'fetch_failed') toast.error('行情更新失败，保留缓存并显示质量状态')
    } catch { /* useTask 已显示错误 */ }
    finally { await snapshot.reload() }
  }
  return <div className="space-y-4">
    <PageHeader title="海外研究" description="港美日韩台证券 · 原币行情 · 交易所日历 · 只读研究" />
    <Card>
      <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (task.running || diagnosisTask.running) return; setCode(input.trim()); setReport(null); setSocial(null) }}>
        <Field label="证券代码" hint="例如 AAPL、HK00700、7203.T、005930.KS、2330.TW"><Input aria-label="海外证券代码" value={input} onChange={(e) => setInput(e.target.value)} /></Field>
        <Button type="submit" disabled={task.running || diagnosisTask.running}>查看</Button><Button loading={task.running} onClick={() => void refresh()}>更新行情</Button>
        <Button loading={diagnosisTask.running} onClick={async () => { try { const r = await diagnosisTask.run(() => api.diagnose(code)); if (r && !r.error) setReport(r) } catch { /* useTask 已显示错误 */ } }}>生成研究报告</Button>
        <Link to="/integrations" className="text-sm text-accent">配置数据与模型</Link>
      </form>
    </Card>
    {snapshot.error && <ErrorBox message={snapshot.error} />}
    {snapshot.loading && <Spinner />}
    {value && <>
      <Card><div className="flex flex-wrap gap-4"><strong>{value.code} · {value.market}</strong><span>{fmt(value.quote.close)} {value.currency}</span><Badge tone={value.quality.status === 'available' ? 'default' : 'warn'}>{value.quality.status === 'available' ? '已核对完整日线' : '数据受限'}</Badge></div>
        <p className="mt-2 text-sm text-muted">行情日期：{value.quote.trade_date ?? '暂无'} · 来源：{value.quote.source ?? '暂无'} · 市场时区：{value.phase.timezone} · 最近完整交易日：{value.phase.effective_daily_bar_date ?? '未知'}</p>
        {value.quality.limitations.length > 0 && <p className="mt-2 text-sm text-warn">行情或日历不完整，报告会降低信心。可更新行情后重试。</p>}
        {value.bars.length ? <CandlestickChart candles={toCandles(value.bars)} /> : <p className="py-10 text-center text-muted">还没有行情，点击“更新行情”获取。</p>}
      </Card>
      {value.market === 'US' && <Card><Button onClick={async () => { try { setSocial(await api.overseasSocial(code)) } catch (e) { toast.error(String(e)) } }}>读取 Reddit / X / Polymarket 情报</Button>{social && <details open className="mt-3"><summary>社交证据与来源（讨论不代表事实）</summary><pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(social, null, 2)}</pre></details>}</Card>}
    </>}
    {report && <DiagnosisView d={report} />}
    <OverseasHoldings onSymbol={(symbol) => { if (task.running || diagnosisTask.running) return; setSocial(null); setInput(symbol); setCode(symbol); setReport(null) }} />
  </div>
}

function OverseasHoldings({ onSymbol }: { onSymbol: (code: string) => void }) {
  const portfolio = useApi(api.overseasPortfolio)
  const refresh = useTask<OverseasPortfolio>()
  const [text, setText] = useState('')
  const [account, setAccount] = useState('海外账户')
  const [asOf, setAsOf] = useState(new Date().toLocaleDateString('en-CA'))
  const [preview, setPreview] = useState<HoldingsImportResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [symbols, setSymbols] = useState<string[]>([])
  async function importCsv(previewOnly: boolean) {
    setBusy(true)
    try { const result = await api.importOverseasHoldings(text, account, asOf, previewOnly); setPreview(result); if (result.saved) { toast.success(`已导入 ${result.saved} 条持仓`); await portfolio.reload() } }
    catch (e) { toast.error(String(e)) } finally { setBusy(false) }
  }
  return <Card>
    <div className="flex flex-wrap items-center gap-3"><h2 className="font-semibold">海外持仓快照</h2><Button loading={refresh.running} onClick={async () => { try { await refresh.run(api.refreshOverseasPortfolio) } catch { /* useTask 已显示错误 */ } finally { await portfolio.reload() } }}>更新持仓行情与汇率</Button><Button onClick={async () => { try { setSymbols((await api.futuSymbols()).codes) } catch (e) { toast.error(String(e)) } }}>读取 Futu 持仓标的</Button></div>
    <p className="my-2 text-sm text-muted">持仓市值合计：{fmt(portfolio.data?.market_value_cny)} CNY（不含现金）。缺价或汇率失效时不合计。快照是导入当日持仓，不会自动同步交易。</p>
    {portfolio.error && <ErrorBox message={portfolio.error} />}
    <div className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr>{['证券', '账户', '数量', '原币市值', '人民币市值', '持仓日期', '汇率日期'].map((label) => <th key={label} className="p-2">{label}</th>)}</tr></thead><tbody>{portfolio.data?.positions.map((row) => <tr key={`${row.account}:${row.code}`} className="border-t border-line"><td className="p-2"><button className="text-accent" onClick={() => onSymbol(row.code)}>{row.code}</button></td><td>{row.account}</td><td>{row.quantity}</td><td>{fmt(row.market_value)} {row.currency}</td><td>{fmt(row.market_value_cny)}</td><td>{row.as_of}</td><td>{row.fx.as_of ?? '缺失'}</td></tr>)}</tbody></table></div>
    {!!symbols.length && <div className="my-3 flex flex-wrap gap-2">{symbols.map((symbol) => <Button key={symbol} onClick={() => onSymbol(symbol)}>{symbol}</Button>)}</div>}
    <details className="mt-4"><summary className="cursor-pointer">导入券商持仓 / 成交 CSV</summary><p className="my-2 text-sm text-muted">支持英文及中文表头：symbol / 证券代码、quantity / 持仓数量、average_cost / 成本价、currency / 币种；数字代码可提供 market / 市场。成交明细另需 side / 买卖方向、trade_date / 成交日期、price / 成交价，可带 fee / 手续费和 trade_id / 成交编号。快照与成交账本使用不同账户名称。导入更新同账户同证券，数量为 0 表示平仓。</p>
      <div className="my-3 grid gap-3 md:grid-cols-3"><Field label="账户"><Input value={account} onChange={(e) => { setAccount(e.target.value); setPreview(null) }} /></Field><Field label="快照日期"><Input type="date" value={asOf} onChange={(e) => { setAsOf(e.target.value); setPreview(null) }} /></Field><Field label="CSV 文件"><Input type="file" accept=".csv,text/csv" onChange={async (e) => { const file = e.target.files?.[0]; if (file) { setText(await file.text()); setPreview(null) } }} /></Field></div>
      <textarea aria-label="券商持仓 CSV" className="w-full rounded border border-line bg-panel p-2" rows={5} value={text} onChange={(e) => { setText(e.target.value); setPreview(null) }} placeholder={'symbol,quantity,average_cost,currency\nAAPL,10,180,USD'} />
      <div className="my-2 flex gap-2"><Button loading={busy} onClick={() => void importCsv(true)}>检查导入</Button><Button disabled={!preview?.ok || busy} onClick={() => void importCsv(false)}>确认导入 {preview?.rows.length ?? 0} 条</Button></div>
      {preview && <p className="text-sm">{preview.ok ? `${preview.rows.length} 条持仓检查通过` : preview.errors.map((e) => `第 ${e.line} 行：${e.error}`).join('；')}</p>}
    </details>
  </Card>
}
