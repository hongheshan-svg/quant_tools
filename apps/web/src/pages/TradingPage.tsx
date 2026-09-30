// 模拟交易：账户、订单（确认/撤单）、持仓、组合风险
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { Order, Position } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { RiskPanel } from '@/components/RiskPanel'
import { Badge, Button, Card, ErrorBox, PageHeader, Stat } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'
import { t, useT } from '@/i18n'
import { toast } from '@/stores/toast'
import { fmtMoney, fmtNum, trendClass } from '@/utils/format'

const ORDER_STATUS: Record<string, [string, 'warn' | 'accent' | 'down' | 'default' | 'up']> = {
  PENDING_CONFIRM: ['待确认', 'warn'], SUBMITTED: ['已报', 'accent'], PARTIAL: ['部分成交', 'accent'], FILLED: ['已成交', 'down'],
  CANCELED: ['已撤销', 'default'], REJECTED: ['已拒绝', 'up'], FAILED: ['失败', 'up'],
}

export function positionColumns(onOpen?: (code: string) => void): Column<Position>[] {
  return [
    { key: 'name', title: t('股票'), render: (p) => <button type="button" className="text-left hover:underline" onClick={() => onOpen?.(p.code)}>{p.name}<div className="num text-xs text-muted">{p.code}</div></button> },
    { key: 'qty', title: t('持仓/可卖'), align: 'right', render: (p) => <span className="num">{p.quantity}/{p.available_quantity}</span> },
    { key: 'cost', title: t('成本价'), align: 'right', render: (p) => <span className="num">{fmtNum(p.avg_cost, 3)}</span> },
    { key: 'price', title: t('最新价'), align: 'right', render: (p) => <span className="num">{fmtNum(p.market_price)}</span> },
    { key: 'stop', title: t('止损/目标'), align: 'right', render: (p) => <span className="num"><span className="text-down">{fmtNum(p.stop_loss)}</span> / <span className="text-up">{fmtNum(p.target_price)}</span></span> },
    { key: 'mv', title: t('市值'), align: 'right', render: (p) => <span className="num">{fmtMoney(p.market_value)}</span> },
    { key: 'pnl', title: t('浮动盈亏'), align: 'right', render: (p) => <span className={`num ${trendClass(p.unrealized_pnl)}`}>{fmtMoney(p.unrealized_pnl, true)}</span> },
  ]
}

export function TradingPage() {
  const t = useT()
  const navigate = useNavigate()
  const snapshot = useApi(api.trading)
  const risk = useApi(api.tradingRisk)
  const prepare = useTask<{ prepared: number; confirmed: number }>()
  const exits = useTask<{ exit_orders: number }>()
  const reloadAll = () => {
    void snapshot.reload()
    void risk.reload()
  }
  const act = async (fn: () => Promise<{ ok: boolean; error?: string }>, success: string) => {
    const r = await fn()
    if (r.ok) toast.success(success)
    else toast.error(r.error ?? t('操作失败'))
    reloadAll()
  }

  const orderColumns: Column<Order>[] = [
    { key: 'time', title: t('创建时间'), render: (o) => <span className="num text-xs">{o.created_at?.slice(0, 16)}</span> },
    { key: 'stock', title: t('股票'), render: (o) => <>{o.name} <span className="num text-xs text-muted">{o.code}</span></> },
    { key: 'side', title: t('方向'), render: (o) => <span className={o.side === 'buy' ? 'text-up' : 'text-down'}>{o.side === 'buy' ? t('买入') : t('卖出')}</span> },
    { key: 'price', title: t('委托价'), align: 'right', render: (o) => <span className="num">{fmtNum(o.price)}</span> },
    { key: 'qty', title: t('数量'), align: 'right', render: (o) => <span className="num">{o.quantity}</span> },
    { key: 'amount', title: t('金额'), align: 'right', render: (o) => <span className="num">{fmtMoney(o.amount)}</span> },
    { key: 'status', title: t('状态'), render: (o) => { const [label, tone] = ORDER_STATUS[o.status] ?? [o.status, 'default']; return <Badge tone={tone}>{t(label)}</Badge> } },
    { key: 'note', title: t('说明'), className: 'max-w-xs text-xs text-muted', render: (o) => o.error_msg || o.risk_note },
    {
      key: 'ops', title: '', align: 'right', render: (o) =>
        o.status === 'PENDING_CONFIRM' ? (
          <div className="flex justify-end gap-1">
            <Button variant="primary" onClick={() => act(() => api.confirmOrder(o.id), t('已确认下单'))}>{t('确认')}</Button>
            <Button variant="ghost" onClick={() => act(() => api.cancelOrder(o.id), t('已撤销'))}>{t('撤销')}</Button>
          </div>
        ) : null,
    },
  ]
  const account = snapshot.data?.account

  return (
    <div>
      <PageHeader
        title={t('模拟交易')}
        description={t('信号生成待确认订单，确认后在模拟盘按委托价成交（T+1）；持仓触及止损价或目标价时生成卖单')}
        actions={
          <>
            <Button loading={prepare.running} onClick={() => prepare.run(api.prepareOrders, { success: (r) => t('新建订单 {n} 笔', { n: r.prepared }) }).then(reloadAll).catch(() => {})}>{t('生成订单')}</Button>
            <Button loading={exits.running} onClick={() => exits.run(api.checkExits, { success: (r) => t('生成卖单 {n} 笔', { n: r.exit_orders ?? 0 }) }).then(reloadAll).catch(() => {})}>{t('检查止损止盈')}</Button>
            <Button onClick={reloadAll}>{t('刷新')}</Button>
          </>
        }
      />
      {snapshot.error && <ErrorBox message={snapshot.error} onRetry={snapshot.reload} />}
      {account && (
        <div className="mb-4 grid grid-cols-2 gap-2 md:grid-cols-4">
          <Stat label={t('总资产')} value={fmtMoney(account.total_assets)} />
          <Stat label={t('可用资金')} value={fmtMoney(account.cash)} />
          <Stat label={t('持仓市值')} value={fmtMoney(account.market_value)} />
          <Stat label={t('浮动盈亏')} value={<span className={trendClass(account.unrealized_pnl)}>{fmtMoney(account.unrealized_pnl, true)}</span>} />
        </div>
      )}
      <div className="space-y-4">
        {risk.data && <Card title={t('组合风险')}><RiskPanel risk={risk.data} /></Card>}
        <Card title={t('持仓')} bodyClassName="p-0">
          <DataTable columns={positionColumns((c) => navigate(`/stocks/${c}`))} rows={snapshot.data?.positions ?? []} rowKey={(p) => p.code} empty={t('暂无持仓')} />
        </Card>
        <Card title={t('订单')} bodyClassName="p-0">
          <DataTable columns={orderColumns} rows={snapshot.data?.orders ?? []} rowKey={(o) => o.id} empty={t('暂无订单')} maxHeight="50vh" />
        </Card>
      </div>
    </div>
  )
}
