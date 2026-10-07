import { useState } from 'react'
import { api } from '@/api/endpoints'
import type { SnapshotCheck } from '@/api/types'
import { Button, Card, ErrorBox, Textarea } from '@/components/ui'
import { useT } from '@/i18n'

export function SnapshotCheckPanel() {
  const t = useT()
  const [text, setText] = useState('{"close":10,"change_pct":4,"amount":200000000,"ma20":9,"high_20":10,"range_20":20,"vol_ratio":2.2,"close_pos":0.9}')
  const [result, setResult] = useState<SnapshotCheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <Card title={t('快照逐条件检查')}><p className="mb-2 text-sm text-muted">{t('只检查下方扁平数值快照，不联网、不补数据、不写选股结果；缺字段显示未知。')}</p><Textarea aria-label={t('候选快照 JSON')} rows={4} value={text} onChange={(event) => setText(event.target.value)} /><Button loading={busy} onClick={async () => { setBusy(true); setError(''); try { setResult(await api.checkScreeningSnapshot(JSON.parse(text) as Record<string, unknown>)) } catch (err) { setError(err instanceof Error ? err.message : String(err)) } finally { setBusy(false) } }}>{t('检查条件')}</Button>{error && <ErrorBox message={error} />}{result?.strategies.map((strategy) => <details key={strategy.strategy} className="my-2"><summary>{strategy.label} · {strategy.matched === null ? t('数据不足') : strategy.matched ? t('命中') : t('未命中')} · {strategy.score ?? '—'}</summary>{strategy.checks.map((check, index) => <p key={index} className="break-all font-mono text-xs">{check.status} · {typeof check.condition === 'string' ? check.condition : JSON.stringify(check.condition)} · {JSON.stringify(check.inputs)}{check.missing.length > 0 && ` · ${t('缺失')} ${check.missing.join(',')}`}</p>)}</details>)}</Card>
}
