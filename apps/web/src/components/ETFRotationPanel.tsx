import { useState } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '@/api/endpoints'
import type { ETFRotationResult, ETFRotationSettings } from '@/api/types'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'
import { Button, Card, ErrorBox, Input, Pct, Select, Spinner } from '@/components/ui'
import { fmtNum } from '@/utils/format'
import { useT } from '@/i18n'

const LIMITS: Record<string, string> = {
  missing_daily_prices: '部分交易日缺少报价', insufficient_backtest_span: '历史长度未达到设置要求',
  safe_asset_unavailable_using_cash: '防守 ETF 缺少有效报价，研究目标改为现金', short_history_signal_only: '不足一年，仅保留研究信号',
  history_does_not_reach_cutoff: '历史报价未覆盖截止日', incomplete_or_invalid_daily_bars_excluded: '已排除盘中或无效日线',
  suspicious_price_jump: '存在异常价格跳变，请核对复权数据', mixed_price_sources: '历史价格包含不同来源，请核对口径',
  execution_calendar_unavailable: '下一执行日缺少正式交易日历', no_executable_rebalance: '没有可执行的调仓记录',
  insufficient_history: '部分标的历史不足',
}

function Editor({ initial }: { initial: ETFRotationSettings }) {
  const t = useT()
  const [form, setForm] = useState(initial)
  const task = useTask<ETFRotationResult>()
  const [result, setResult] = useState<ETFRotationResult | null>(null)
  const [error, setError] = useState('')
  const set = <K extends keyof ETFRotationSettings>(key: K, value: ETFRotationSettings[K]) => setForm({ ...form, [key]: value })
  return <div className="space-y-3 text-sm">
    <p className="text-muted">{t('固定国内 ETF 池；正动量入选、固定槽位、防守资产或现金；周/月末信号在下一交易日收盘执行。')}</p>
    <div className="grid gap-3 sm:grid-cols-3">
      <label>{t('风险 ETF 池')}<Input value={form.risk_assets.join(',')} onChange={(e) => set('risk_assets', e.target.value.split(',').map((v) => v.trim()).filter(Boolean))} /></label>
      <label>{t('防守 ETF（留空为现金）')}<Input value={form.safe_asset} onChange={(e) => set('safe_asset', e.target.value)} /></label>
      <label>{t('周期')}<Select value={form.rebalance} onChange={(e) => set('rebalance', e.target.value as ETFRotationSettings['rebalance'])}><option value="weekly">{t('每周')}</option><option value="monthly">{t('每月')}</option></Select></label>
      {(['start', 'end'] as const).map((key) => <label key={key}>{t(key === 'start' ? '开始日期' : '截止日期（留空为已收盘日）')}<Input type="date" value={form[key]} onChange={(e) => set(key, e.target.value)} /></label>)}
      {(['lookback_days', 'top_n', 'switch_buffer_pct', 'cost_bps'] as const).map((key, i) => <label key={key}>{t(['动量交易日数', '持有槽数', '换仓缓冲（百分点）', '单边费用（基点）'][i])}<Input type="number" min={0} value={form[key]} onChange={(e) => set(key, Number(e.target.value))} /></label>)}
    </div>
    <label className="flex gap-2"><input type="checkbox" checked={form.refresh} onChange={(e) => set('refresh', e.target.checked)} />{t('先联网更新前复权历史')}</label>
    <Button loading={task.running} onClick={() => { setError(''); void task.run(() => api.etfRotationRun(form)).then(setResult).catch((err: unknown) => setError(err instanceof Error ? err.message : String(err))) }}>{t('运行 ETF 轮动回测')}</Button>
    {error && <ErrorBox message={error} />}
    {result && <div className="space-y-3">
      <p>{result.as_of} · {t(result.status)} · {t('快照')} {result.price_snapshot_hash.slice(0, 12)}</p>
      {result.limitations.length > 0 && <p role="status" className="text-warn">{result.limitations.map((reason) => { const [key, detail] = reason.split(':'); return `${t(LIMITS[key] ?? key)}${detail ? `：${detail}` : ''}` }).join('；')}</p>}
      {result.performance_available === false ? <p className="text-warn">{t('有效回测不足一年，仅展示研究信号；不展示年化指标、年度收益、调仓明细与参数扫描。')}</p> : <div className="flex flex-wrap gap-5">{['total_return', 'cagr', 'max_drawdown', 'ann_vol', 'sharpe', 'calmar'].map((key, i) => <span key={key}>{t(['总收益', '年化收益', '最大回撤', '年化波动', '夏普（无风险=0）', '卡玛'][i])} {key === 'sharpe' || key === 'calmar' ? fmtNum(result.metrics[key]) : <Pct value={result.metrics[key] == null ? null : result.metrics[key]! * 100} />}</span>)}</div>}
      <div className="h-56"><ResponsiveContainer width="100%" height="100%"><LineChart data={result.curve}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="date" /><YAxis domain={['auto', 'auto']} /><Tooltip /><Legend /><Line dataKey="equity" name={t('轮动净值')} dot={false} stroke="#3b82f6" /><Line dataKey="benchmark" name={t('等权风险池基准')} dot={false} stroke="#94a3b8" /></LineChart></ResponsiveContainer></div>
      <p>{t('基准总收益')} <Pct value={result.benchmark_metrics.total_return == null ? null : result.benchmark_metrics.total_return * 100} /> · {t('下一执行日')} {result.next_action.execution_date ?? t('尚未到调仓日')} · {Object.entries(result.next_action.weights).map(([code, weight]) => `${code} ${(weight * 100).toFixed(1)}%`).join('、')}</p>
      <p>{t('当前持仓权重')}：{Object.entries(result.current_weights).map(([code, weight]) => `${code} ${(weight * 100).toFixed(1)}%`).join('、')} · {t('目标仅供研究，成交须等待执行日有效报价')}</p>
      {result.next_action.quotes_available === false && <p className="text-warn">{t('持仓或目标缺报价，不能确认调仓成交')}</p>}
      {result.curve.some((row) => row.missing_prices?.length) && <p className="text-warn">{t('曲线包含缺报价交易日，持仓估值沿用已知价格，不代表当天可成交。')}</p>}
      {result.performance_available !== false && <><details><summary>{t('年度收益与参数稳定性')}</summary><div className="flex flex-wrap gap-4">{Object.entries(result.annual_returns).map(([year, value]) => <span key={year}>{year} <Pct value={value == null ? null : value * 100} /></span>)}</div>{result.parameter_sweep.map((row) => <p key={row.lookback_days}>{row.lookback_days} {t('交易日')} · <Pct value={row.total_return == null ? null : row.total_return * 100} /> · {t('回撤')} <Pct value={row.max_drawdown == null ? null : row.max_drawdown * 100} /></p>)}</details>
      <details><summary>{t('调仓记录')} ({result.trades.length})</summary>{result.trades.map((row) => <p key={row.execution_date}>{row.signal_date} → {row.execution_date} · {Object.entries(row.to_weights).map(([code, weight]) => `${code} ${(weight * 100).toFixed(1)}%`).join('、')} · {t('换手')} {(row.turnover * 100).toFixed(1)}%</p>)}</details></>}
      <p className="text-xs text-muted">{result.note}</p>
    </div>}
  </div>
}

export function ETFRotationPanel() {
  const t = useT()
  const settings = useApi(api.etfRotationSettings)
  return <Card title={t('ETF 双动量轮动')}>{settings.error ? <ErrorBox message={settings.error} onRetry={settings.reload} /> : settings.data ? Array.isArray(settings.data.risk_assets) ? <Editor initial={settings.data} /> : <ErrorBox message={t('轮动配置格式无效')} onRetry={settings.reload} /> : <Spinner />}</Card>
}
