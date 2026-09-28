// 策略选股：选股结果（含次日涨幅）、近 30 天策略次日表现、历史回测
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { BacktestReport, BacktestStrategy, ScreeningPick, ScreenResult, StrategyPerformance } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, PageHeader, Pct, Spinner } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { progressText, useTask } from '@/hooks/useTask'
import { fmtNum } from '@/utils/format'

const rate = (v: number | null) => <Pct value={v} digits={1} signed={false} />

export function ScreeningPage() {
  const navigate = useNavigate()
  const { data, error, loading, reload } = useApi(api.screening)
  const screen = useTask<ScreenResult>()
  const backtest = useTask<BacktestReport>()

  const pickColumns: Column<ScreeningPick>[] = [
    { key: 'stock', title: '股票', render: (p) => <><div>{p.name}</div><div className="num text-xs text-muted">{p.code}</div></> },
    { key: 'labels', title: '策略', render: (p) => <div className="flex flex-wrap gap-1">{p.labels.map((l) => <Badge key={l} tone="accent">{l}</Badge>)}</div> },
    { key: 'score', title: '得分', align: 'right', render: (p) => <span className="num">{fmtNum(p.score, 0)}</span> },
    { key: 'fit', title: '大盘适配', render: (p) => (p.fits_regime ? <span className="text-down">适配</span> : <span className="text-muted">不适配</span>) },
    { key: 'pct', title: '当日涨幅', align: 'right', render: (p) => <Pct value={p.change_pct} /> },
    { key: 'next', title: '次日涨幅', align: 'right', render: (p) => <Pct value={p.next_change_pct} /> },
    { key: 'reason', title: '入选理由', className: 'max-w-lg text-xs text-muted', render: (p) => p.reasons.join('；') },
  ]
  const perfColumns: Column<StrategyPerformance>[] = [
    { key: 'label', title: '策略', render: (r) => r.label },
    { key: 'regimes', title: '适配环境', render: (r) => <span className="text-muted">{r.regimes}</span> },
    { key: 'picks', title: '近30天入选', align: 'right', render: (r) => <span className="num">{r.picks}</span> },
    { key: 'eval', title: '已验证', align: 'right', render: (r) => <span className="num">{r.evaluated}</span> },
    { key: 'avg', title: '次日平均涨幅', align: 'right', render: (r) => <Pct value={r.avg_next_pct} /> },
    { key: 'win', title: '次日上涨比例', align: 'right', render: (r) => rate(r.win_rate) },
    { key: 'lu', title: '次日涨停比例', align: 'right', render: (r) => rate(r.limit_up_rate) },
  ]
  const btColumns: Column<BacktestStrategy>[] = [
    { key: 'label', title: '策略', render: (r) => r.label },
    { key: 'days', title: '天数/入选', align: 'right', render: (r) => <span className="num">{r.days}/{r.picks}</span> },
    { key: 'a1', title: '次日均收益', align: 'right', render: (r) => <Pct value={r.avg_1d} /> },
    { key: 'w1', title: '次日胜率', align: 'right', render: (r) => rate(r.win_1d) },
    { key: 'a3', title: '3日均收益', align: 'right', render: (r) => <Pct value={r.avg_3d} /> },
    { key: 'a5', title: '5日均收益', align: 'right', render: (r) => <Pct value={r.avg_5d} /> },
    { key: 'lu', title: '次日涨停率', align: 'right', render: (r) => rate(r.limit_up_rate) },
    { key: 'fit', title: '适配时次日均收益', align: 'right', render: (r) => <Pct value={r.avg_1d_fit} /> },
    { key: 'total', title: '累计收益', align: 'right', render: (r) => <Pct value={r.total_return} /> },
    { key: 'mdd', title: '最大回撤', align: 'right', render: (r) => <Pct value={r.max_drawdown} /> },
    { key: 'weight', title: '排序权重', align: 'right', render: (r) => <span className="num">{fmtNum(r.weight)}</span> },
  ]
  const bt = data?.backtest

  return (
    <div>
      <PageHeader
        title="策略选股"
        description="放量突破、强势未板、龙回头、主线补涨、缩量回踩、超跌反弹 6 个策略扫描全市场；与大盘环境适配的排在前面"
        actions={
          <>
            <Button loading={backtest.running} onClick={() => backtest.run(() => api.backtest(60), { success: (r) => (r.note ? r.note : `回测完成：${r.dates} 个交易日`) }).then(reload).catch(() => {})}>
              {backtest.running ? `回测中 ${progressText(backtest.progress)}` : '历史回测（60 天）'}
            </Button>
            <Button variant="primary" loading={screen.running} onClick={() => screen.run(api.runScreening, { success: (r) => `选出 ${r.picks.length} 只${r.notes.length ? '（' + r.notes[0] + '）' : ''}` }).then(reload).catch(() => {})}>
              重新选股
            </Button>
          </>
        }
      />
      {error && <ErrorBox message={error} onRetry={reload} />}
      {loading && !data ? <Spinner /> : (
        <div className="space-y-4">
          <Card title="选股结果" bodyClassName="p-0">
            <DataTable columns={pickColumns} rows={data?.picks ?? []} rowKey={(p) => p.code} onRowClick={(p) => navigate(`/stocks/${p.code}`)} empty="暂无选股结果，点「重新选股」扫描全市场" />
          </Card>
          <Card title="策略次日表现（近 30 天实际选股）" bodyClassName="p-0">
            <DataTable columns={perfColumns} rows={data?.performance ?? []} rowKey={(r) => r.strategy} />
          </Card>
          <Card
            title={bt ? `历史回测 ${bt.start} ~ ${bt.end}（${bt.dates} 个交易日${bt.skipped_dates ? `，${bt.skipped_dates} 天行情不全已跳过` : ''}）` : '历史回测'}
            actions={bt && <span className="text-xs text-muted">{bt.created_at} 生成 · 次日开盘入场</span>}
            bodyClassName="p-0"
          >
            <DataTable columns={btColumns} rows={bt?.strategies ?? []} rowKey={(r) => r.strategy} empty={bt?.note ?? '尚未回测'} />
          </Card>
        </div>
      )}
    </div>
  )
}
