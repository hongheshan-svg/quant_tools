// 策略选股：选股结果（含次日涨幅）、近 30 天策略次日表现、历史回测
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { BacktestReport, BacktestStrategy, ScreeningDate, ScreeningPick, ScreenResult, StrategyPerformance } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, PageHeader, Pct, Select, Spinner } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
import { progressText, useTask } from '@/hooks/useTask'
import { fmtNum } from '@/utils/format'
import { ETFRotationPanel } from '@/components/ETFRotationPanel'
import { SourceRunHistory } from '@/components/SourceRunHistory'
import { SnapshotCheckPanel } from '@/components/SnapshotCheckPanel'

const rate = (v: number | null) => <Pct value={v} digits={1} signed={false} />

export function ScreeningPage() {
  const t = useT()
  const navigate = useNavigate()
  const { data, error, loading, reload } = useApi(api.screening)
  const datesApi = useApi(api.screeningDates)
  const [selDate, setSelDate] = useState('')
  const [strategy, setStrategy] = useState('')
  const [risk, setRisk] = useState('')
  const [minQuality, setMinQuality] = useState('')
  const [histPicks, setHistPicks] = useState<ScreeningPick[] | null>(null)
  const [histError, setHistError] = useState('')
  const bt = data?.backtest
  const t1 = bt?.engine_version === 'a-share-t1-v2'
  const screen = useTask<ScreenResult>()
  const backtest = useTask<BacktestReport>()

  // 默认（最近一天、全部策略）直接用 /screening 的结果，切换日期或策略后请求 /screening/picks
  useEffect(() => {
    if (!selDate && !strategy) {
      setHistPicks(null)
      setHistError('')
      return
    }
    let cancelled = false
    api.screeningPicks(selDate || undefined, strategy || undefined)
      .then((r) => { if (!cancelled) { setHistPicks(r.picks); setHistError('') } })
      .catch((e) => { if (!cancelled) setHistError(e instanceof Error ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [selDate, strategy])

  const refreshAll = () => {
    setSelDate('')
    setStrategy('')
    void datesApi.reload()
    return reload()
  }
  const dates = datesApi.data?.dates ?? []
  const strategyOptions = datesApi.data?.strategies ?? []
  const dateLabel = (d: ScreeningDate) => {
    const next = d.avg_next_pct == null ? '' : `，${t('次日均')} ${d.avg_next_pct > 0 ? '+' : ''}${d.avg_next_pct.toFixed(2)}%`
    return `${d.trade_date}（${t('{n} 只', { n: d.picks })}${next}）`
  }
  const histColumns: Column<ScreeningDate>[] = [
    { key: 'date', title: t('日期'), render: (d) => <span className="num">{d.trade_date}</span> },
    { key: 'picks', title: t('入选数'), align: 'right', render: (d) => <span className="num">{d.picks}</span> },
    { key: 'eval', title: t('已验证'), align: 'right', render: (d) => <span className="num">{d.evaluated}</span> },
    { key: 'avg', title: t('次日均涨幅'), align: 'right', render: (d) => <Pct value={d.avg_next_pct} /> },
    { key: 'win', title: t('次日上涨比例'), align: 'right', render: (d) => rate(d.win_rate) },
  ]
  const shownPicks = (histPicks ?? data?.picks ?? []).filter((p) => (!risk || p.risk_level === risk) && (!minQuality || (p.data_quality?.score ?? 0) >= Number(minQuality)))

  const pickColumns: Column<ScreeningPick>[] = [
    { key: 'stock', title: t('股票'), render: (p) => <><div>{p.name}</div><div className="num text-xs text-muted">{p.code}</div></> },
    { key: 'labels', title: t('策略'), render: (p) => <div className="flex flex-wrap gap-1">{p.labels.map((l) => <Badge key={l} tone="accent">{t(l)}</Badge>)}</div> },
    { key: 'score', title: t('得分'), align: 'right', render: (p) => <div><span className="num">{fmtNum(p.score, 0)}</span>{p.factor_scores && <details className="mt-1 text-left text-xs" onClick={(e) => e.stopPropagation()}><summary className="cursor-pointer text-accent">{t('评分明细')}</summary><div className="min-w-36 space-y-1 py-2"><p>{t('初始评分')}：{p.screen_score ?? '—'}</p><p>{t('模型评分')}：{p.llm_score ?? '—'}</p><p>{t('风险扣分')}：{p.risk_penalty ?? 0}</p><p>{t('集中度扣分')}：{p.portfolio_penalty ?? 0}</p>{Object.entries(p.factor_scores).map(([key, value]) => <p key={key}>{t(({ value: '估值', quality: '盈利质量', liquidity: '流动性', momentum: '动量', activity: '活跃度', stability: '稳定性', reversal: '反转', size: '规模', theme_heat: '题材热度' } as Record<string, string>)[key] ?? key)}：{value == null ? t('缺失') : fmtNum(value, 1)}</p>)}</div></details>}</div> },
    { key: 'quality', title: t('数据质量'), render: (p) => <div title={(p.data_quality?.flags ?? []).join('；')}><span className="num">{p.data_quality?.score ?? '—'}</span><div className="text-xs text-muted">{p.data_quality?.source ?? t('来源未知')} · {p.factor_coverage === undefined ? '—' : `${Math.round(p.factor_coverage * 100)}%`}</div></div> },
    { key: 'risk', title: t('风险'), render: (p) => <div title={p.risk_flags?.join('；')}><Badge tone={p.risk_level === 'high' ? 'warn' : 'default'}>{p.risk_level ? t(({ high: '高', medium: '中', low: '低' } as Record<string, string>)[p.risk_level]) : '—'}</Badge><div className="text-xs text-muted">-{p.risk_penalty ?? 0} / -{p.portfolio_penalty ?? 0}</div></div> },
    { key: 'theme', title: t('行业与题材'), render: (p) => p.industry || p.themes?.join('、') || '—' },
    { key: 'llm', title: t('模型复核'), render: (p) => <span title={p.llm_reason}>{p.llm_score ?? '—'}{p.post_analysis?.report_id && <Button onClick={(e) => { e.stopPropagation(); navigate(`/history?id=${p.post_analysis?.report_id}`) }}>{t('研究报告')}</Button>}</span> },
    { key: 'fit', title: t('大盘适配'), render: (p) => (p.fits_regime ? <span className="text-down">{t('适配')}</span> : <span className="text-muted">{t('不适配')}</span>) },
    { key: 'pct', title: t('当日涨幅'), align: 'right', render: (p) => <Pct value={p.change_pct} /> },
    { key: 'next', title: t('次日涨幅'), align: 'right', render: (p) => <Pct value={p.next_change_pct} /> },
    { key: 'reason', title: t('入选理由'), className: 'max-w-lg text-xs text-muted', render: (p) => <div>{p.reasons.join('；')}{[p.why_selected, p.why_now].some((items) => items?.length) && <details onClick={(event) => event.stopPropagation()}><summary>{t('入选依据与近期催化')}</summary>{[...p.why_selected ?? [], ...p.why_now ?? []].map((item, index) => <p key={index} className="my-1">[{t(item.kind === 'observed' ? '观测' : item.kind === 'inferred' ? '推断' : '未知')}] {item.text} · {item.source ?? t('来源未知')} · {item.status} · {item.data_date ?? t('观测日期未知')}{item.age_days != null && ` · ${item.age_days} ${t('天前')}`} · {t('取得时间')} {item.fetched_at ?? '—'}{item.url && <a href={item.url} onClick={(event) => event.stopPropagation()} target="_blank" rel="noreferrer" className="ml-1 text-accent">{t('来源')}</a>}</p>)}</details>}</div> },
  ]
  const perfColumns: Column<StrategyPerformance>[] = [
    { key: 'label', title: t('策略'), render: (r) => t(r.label) },
    { key: 'regimes', title: t('适配环境'), render: (r) => <span className="text-muted">{r.regimes}</span> },
    { key: 'picks', title: t('近30天入选'), align: 'right', render: (r) => <span className="num">{r.picks}</span> },
    { key: 'eval', title: t('已验证'), align: 'right', render: (r) => <span className="num">{r.evaluated}</span> },
    { key: 'avg', title: t('次日平均涨幅'), align: 'right', render: (r) => <Pct value={r.avg_next_pct} /> },
    { key: 'win', title: t('次日上涨比例'), align: 'right', render: (r) => rate(r.win_rate) },
    { key: 'lu', title: t('次日涨停比例'), align: 'right', render: (r) => rate(r.limit_up_rate) },
  ]
  const btColumns: Column<BacktestStrategy>[] = [
    { key: 'label', title: t('策略'), render: (r) => t(r.label) },
    { key: 'days', title: t('天数/入选'), align: 'right', render: (r) => <span className="num">{r.days}/{r.picks}</span> },
    { key: 'eval', title: t('有效/缺失/未买入'), align: 'right', render: (r) => <span className="num">{r.evaluated}/{r.unavailable ?? 0}/{r.entry_blocked ?? 0}</span> },
    { key: 'a1', title: t(t1 ? '持有1日均收益' : '次日均收益'), align: 'right', render: (r) => <Pct value={r.avg_1d} /> },
    { key: 'w1', title: t(t1 ? '持有1日上涨比例' : '次日胜率'), align: 'right', render: (r) => rate(r.win_1d) },
    { key: 'a3', title: t('3日均收益'), align: 'right', render: (r) => <Pct value={r.avg_3d} /> },
    { key: 'a5', title: t('5日均收益'), align: 'right', render: (r) => <Pct value={r.avg_5d} /> },
    { key: 'lu', title: t('次日涨停率'), align: 'right', render: (r) => rate(r.limit_up_rate) },
    { key: 'fit', title: t(t1 ? '适配时持有1日均收益' : '适配时次日均收益'), align: 'right', render: (r) => <Pct value={r.avg_1d_fit} /> },
    { key: 'total', title: t('样本复利'), align: 'right', render: (r) => <Pct value={r.total_return} /> },
    { key: 'mdd', title: t('样本回撤'), align: 'right', render: (r) => <Pct value={r.max_drawdown} /> },
    { key: 'weight', title: t('排序权重'), align: 'right', render: (r) => <span className="num">{fmtNum(r.weight)}</span> },
  ]

  return (
    <div>
      <PageHeader
        title={t('策略选股')}
        description={t('短线策略与自定义规则扫描全市场；数据不完整时保留上次成功结果')}
        actions={
          <>
            <Button loading={backtest.running} onClick={() => backtest.run(() => api.backtest(60), { success: (r) => (r.note ? r.note : t('回测完成：{n} 个交易日', { n: r.dates })) }).then(reload).catch(() => {})}>
              {backtest.running ? `${t('回测中')} ${progressText(backtest.progress)}` : t('历史回测（60 天）')}
            </Button>
            <Button variant="primary" loading={screen.running} onClick={() => screen.run(api.runScreening, { success: (r) => t('选出 {n} 只', { n: r.picks.length }) + (r.notes.length ? '（' + r.notes[0] + '）' : '') }).then(refreshAll).catch(() => {})}>
              {t('重新选股')}
            </Button>
          </>
        }
      />
      {error && <ErrorBox message={error} onRetry={reload} />}
      {loading && !data ? <Spinner /> : (
        <div className="space-y-4">
          {data?.last_run && <Card title={t('最近运行')}><p className="text-sm">{data.last_run.created_at.slice(0, 19).replace('T', ' ')} · {t(data.last_run.status === 'success' ? '已完成' : '数据不完整')} · {t('行情样本 {n} 只', { n: data.last_run.stats.universe ?? 0 })}</p>{data.last_run.notes.map((note) => <p key={note} className="mt-1 text-xs text-muted">{note}</p>)}</Card>}
          <Card
            title={t('选股结果')}
            actions={
              <div className="flex gap-2">
                <Select aria-label={t('选股日期')} value={selDate} onChange={(e) => setSelDate(e.target.value)}>
                  <option value="">{t('最近一天')}</option>
                  {dates.map((d) => <option key={d.trade_date} value={d.trade_date}>{dateLabel(d)}</option>)}
                </Select>
                <Select aria-label={t('策略筛选')} value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                  <option value="">{t('全部策略')}</option>
                  {strategyOptions.map((s) => <option key={s.name} value={s.name}>{t(s.label)}</option>)}
                </Select>
                <Select aria-label={t('风险筛选')} value={risk} onChange={(e) => setRisk(e.target.value)}><option value="">{t('全部风险')}</option>{['low', 'medium', 'high'].map((r) => <option key={r}>{r}</option>)}</Select>
                <Select aria-label={t('数据质量筛选')} value={minQuality} onChange={(e) => setMinQuality(e.target.value)}><option value="">{t('全部质量')}</option><option value="75">≥75</option><option value="90">≥90</option></Select>
              </div>
            }
            bodyClassName="p-0"
          >
            {histError && <ErrorBox message={histError} />}
            <DataTable columns={pickColumns} rows={shownPicks} rowKey={(p) => p.code} onRowClick={(p) => navigate(`/stocks/${p.code}`)} empty={t('暂无选股结果，点「重新选股」扫描全市场')} />
          </Card>
          <Card title={t('历史概览（最近 20 个交易日）')} bodyClassName="p-0">
            <DataTable columns={histColumns} rows={dates.slice(0, 20)} rowKey={(d) => d.trade_date} onRowClick={(d) => setSelDate(d.trade_date)} empty={t('暂无历史选股')} />
          </Card>
          <Card title={t('策略次日表现（近 30 天实际选股）')} bodyClassName="p-0">
            <DataTable columns={perfColumns} rows={data?.performance ?? []} rowKey={(r) => r.strategy} />
          </Card>
          <Card
            title={bt ? `${t('历史回测')} ${bt.start} ~ ${bt.end}（${t('{n} 个交易日', { n: bt.dates })}${bt.skipped_dates ? `，${t('{n} 天行情不全已跳过', { n: bt.skipped_dates })}` : ''}）` : t('历史回测')}
            actions={bt && <span className="text-xs text-muted">{t('{time} 生成', { time: bt.created_at })} · {t('次日开盘入场')}</span>}
            bodyClassName="p-0"
          >
            {bt && <div className="space-y-1 border-b border-line p-4 text-xs text-muted">
              {bt.note && <p role="status">{bt.note}</p>}
              <p>{bt.methodology ?? t('旧版回测包含当日买卖口径，请重新回测；旧权重不再用于排序。')}</p>
              {bt.benchmark && <p>{t('同期基准')} {bt.benchmark.name} · {bt.benchmark.samples} / {bt.benchmark.samples + bt.benchmark.missing} · <Pct value={bt.benchmark.avg_1d} /> · {bt.benchmark.status}</p>}
              {t1 && <p>{t('有效为已观察到持有1日退出价的样本；缺失表示成熟样本缺行情，未买入包括一字涨停或零成交。未到退出日的样本不计收益。')}</p>}
            </div>}
            <DataTable columns={btColumns} rows={bt?.strategies ?? []} rowKey={(r) => r.strategy} empty={bt?.note ?? t('尚未回测')} />
          </Card>
        </div>
      )}
      <div className="mt-4"><SourceRunHistory revision={screen.running ? 1 : 2} /></div>
      <div className="mt-4"><ETFRotationPanel /></div>
      <div className="mt-4"><SnapshotCheckPanel /></div>
    </div>
  )
}
