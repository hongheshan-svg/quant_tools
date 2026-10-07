import { useState } from 'react'
import { api } from '@/api/endpoints'
import type { DataCapability, SourceStatus } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { DataSourceSettingsPanel } from '@/components/DataSourceSettingsPanel'
import { Badge, Button, Card, ErrorBox, PageHeader, Tabs } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
import { fmtNum } from '@/utils/format'

const STATUS: Record<string, [string, 'down' | 'warn' | 'up']> = { ok: ['正常', 'down'], failing: ['失败', 'warn'], circuit_open: ['熔断中', 'up'] }
const HEALTH: Record<string, [string, 'down' | 'warn' | 'up' | 'default']> = {
  ok: ['正常', 'down'], failing: ['失败', 'warn'], open: ['熔断中', 'up'], unknown: ['未知', 'default'],
}
const time = (v: string | null) => (v ? v.replace('T', ' ') : '--')

function Capabilities() {
  const t = useT()
  const { data, error, loading, reload } = useApi(api.capabilities)
  const center = useApi(api.dataCenter)
  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-sm text-muted">{t('各数据集按回退顺序列出数据源；健康记录可跨服务重启恢复，未运行过为「未知」。')}</p>
        <Button onClick={reload} loading={loading}>{t('刷新')}</Button>
      </div>
      {error && <ErrorBox message={error} onRetry={reload} />}
      {center.error && <ErrorBox message={center.error} onRetry={center.reload} />}
      <Card title={t('本地数据质量')}><p className="mb-2 text-xs text-muted">{t('已配置和请求成功不代表数据完整；本页只读，不触发网络探测。')}</p>{center.data?.snapshots?.map((row) => <p key={row.dataset} className="text-sm">{row.dataset} · {row.status} · {t('观测日期')} {row.trade_date ?? '—'} / {row.expected_date ?? '—'} · {row.rows_on_date} {t('行')} · {t('取得时间')} {row.fetched_at ?? '—'}<span className="block text-xs text-muted">{row.note}</span></p>)}</Card>
      <Card title={t('供应商 × 数据集 × 场景')}><div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr>{['提供方', '数据集', '标的范围', '使用场景', '优先级 / 配置来源', '观测 / 取得时间'].map((label) => <th key={label} className="p-2">{t(label)}</th>)}</tr></thead><tbody>{center.data?.matrix?.map((row) => <tr key={`${row.provider}:${row.dataset}`} className="border-t border-line"><td className="p-2">{row.provider_label}<p>{row.configured ? t('已配置') : t('未配置')}</p></td><td>{row.dataset}</td><td>{row.markets.join(',')} · {row.asset_kinds.join(',')}</td><td>{row.scenarios.join(',')}<p className="text-muted">{row.limitations}</p></td><td>{row.priority} · {row.configuration_origin}</td><td>{row.observation_timestamp ?? t('未知')} / {row.fetched_at ?? t('未知')}</td></tr>)}</tbody></table></div></Card>
      <div className="space-y-3">
        {(data ?? []).map((ds: DataCapability) => (
          <Card key={ds.dataset} title={ds.label} bodyClassName="p-0">
            {ds.sources.length === 0 ? (
              <p className="px-4 py-3 text-sm text-muted">{t('暂无已配置的数据源')}</p>
            ) : (
              <ol className="divide-y divide-line">
                {ds.sources.map((s, i) => {
                  const [text, tone] = HEALTH[s.health.status] ?? HEALTH.unknown
                  return (
                    <li key={s.name} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm">
                      <span className="num w-5 text-muted">{i + 1}</span>
                      <span className="font-medium">{s.label}</span>
                      <Badge tone={s.configured ? 'down' : 'warn'}>{s.configured ? t('已配置') : t('未配置')}</Badge>
                      <Badge tone={tone}>{t(text)}</Badge>
                      {s.note && <span className="text-xs text-muted">{s.note}</span>}
                      {s.health.last_error && <span className="max-w-full text-xs text-muted">{t('最近错误：')}{s.health.last_error}</span>}
                    </li>
                  )
                })}
              </ol>
            )}
          </Card>
        ))}
      </div>
    </div>
  )
}

export function SourcesPage() {
  const t = useT()
  const [tab, setTab] = useState<'status' | 'capabilities' | 'settings'>('status')
  return (
    <div>
      <PageHeader title={t('数据源状态')} description={t('各数据源的成功、失败与熔断记录可跨重启恢复；连续失败 3 次后暂停 5 分钟再试。')} />
      <Tabs<'status' | 'capabilities' | 'settings'> tabs={[{ key: 'status', label: t('运行状态') }, { key: 'capabilities', label: t('能力总览') }, { key: 'settings', label: t('来源与优先级') }]} value={tab} onChange={setTab} />
      {tab === 'status' ? <StatusTable /> : tab === 'settings' ? <DataSourceSettingsPanel /> : <Capabilities />}
    </div>
  )
}

function StatusTable() {
  const t = useT()
  const { data, error, loading, reload } = useApi(api.sources)
  const columns: Column<SourceStatus>[] = [
    { key: 'dataset', title: t('数据集'), render: (r) => r.dataset },
    { key: 'source', title: t('数据源'), render: (r) => r.source },
    { key: 'status', title: t('状态'), render: (r) => <Badge tone={(STATUS[r.status] ?? ['', 'warn'])[1]}>{t((STATUS[r.status] ?? [r.status])[0])}</Badge> },
    { key: 'ok', title: t('最近成功'), render: (r) => <span className="num">{time(r.last_success)}</span> },
    { key: 'fail', title: t('最近失败'), render: (r) => <span className="num">{time(r.last_failure)}</span> },
    { key: 'streak', title: t('连续失败'), align: 'right', render: (r) => <span className="num">{r.consecutive_failures}</span> },
    { key: 'total', title: t('累计成功/失败'), align: 'right', render: (r) => <span className="num">{r.total_success}/{r.total_failure}</span> },
    { key: 'elapsed', title: t('耗时(秒)'), align: 'right', render: (r) => <span className="num">{fmtNum(r.last_elapsed, 2)}</span> },
    { key: 'error', title: t('最近错误'), className: 'max-w-sm text-xs text-muted', render: (r) => r.last_error },
  ]
  return (
    <div>
      <div className="mb-3 flex justify-end"><Button onClick={reload} loading={loading}>{t('刷新')}</Button></div>
      {data && <div className="mb-4 grid gap-3 sm:grid-cols-3">{[
        [t('已运行来源'), data.length], [t('正常来源'), data.filter((r) => r.status === 'ok').length], [t('需要关注'), data.filter((r) => r.status !== 'ok').length],
      ].map(([label, value]) => <Card key={label} bodyClassName="p-3"><p className="text-xs text-muted">{label}</p><p className="num mt-1 text-xl">{value}</p></Card>)}</div>}
      {error && <ErrorBox message={error} onRetry={reload} />}
      <Card bodyClassName="p-0">
        <DataTable columns={columns} rows={data ?? []} rowKey={(r) => `${r.dataset}-${r.source}`} empty={t('采集运行后显示')} />
      </Card>
    </div>
  )
}
