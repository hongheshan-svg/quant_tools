// 实盘记账：手动记一笔、导入交割单、设置可用资金和止损止盈；只记账，不连券商、不下单
import { Trash2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { Position, RealTrade } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { RiskPanel } from '@/components/RiskPanel'
import { Button, Card, ErrorBox, Field, Input, Modal, PageHeader, Select, Stat } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { toast } from '@/stores/toast'
import { fmtMoney, fmtNum, trendClass } from '@/utils/format'
import { positionColumns } from './TradingPage'

const today = () => new Date().toISOString().slice(0, 10)
const EMPTY_TRADE = { trade_date: today(), trade_time: '', code: '', side: 'buy', price: '', quantity: '100', fee: '0', note: '' }

export function RealPage() {
  const navigate = useNavigate()
  const { data, error, reload } = useApi(api.real)
  const [tradeOpen, setTradeOpen] = useState(false)
  const [trade, setTrade] = useState(EMPTY_TRADE)
  const [cashOpen, setCashOpen] = useState(false)
  const [cash, setCash] = useState('')
  const [plan, setPlan] = useState<{ code: string; name: string; stop: string; target: string } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const submitTrade = async () => {
    try {
      await api.addRealTrade({ ...trade, price: Number(trade.price), quantity: Number(trade.quantity), fee: Number(trade.fee || 0) })
      toast.success('已记录')
      setTradeOpen(false)
      setTrade({ ...EMPTY_TRADE, trade_date: today() })
      void reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    }
  }

  const tradeColumns: Column<RealTrade>[] = [
    { key: 'date', title: '日期', render: (t) => <span className="num">{t.trade_date} <span className="text-xs text-muted">{t.trade_time}</span></span> },
    { key: 'stock', title: '股票', render: (t) => <>{t.name} <span className="num text-xs text-muted">{t.code}</span></> },
    { key: 'side', title: '方向', render: (t) => <span className={t.side === 'buy' ? 'text-up' : 'text-down'}>{t.side === 'buy' ? '买入' : '卖出'}</span> },
    { key: 'price', title: '成交价', align: 'right', render: (t) => <span className="num">{fmtNum(t.price, 3)}</span> },
    { key: 'qty', title: '数量', align: 'right', render: (t) => <span className="num">{t.quantity}</span> },
    { key: 'fee', title: '费用', align: 'right', render: (t) => <span className="num">{fmtNum(t.fee)}</span> },
    { key: 'source', title: '来源', render: (t) => <span className="text-xs text-muted">{t.source === 'import' ? '导入' : '手动'}</span> },
    { key: 'note', title: '备注', className: 'text-xs text-muted', render: (t) => t.note },
    {
      key: 'del', title: '', align: 'right', render: (t) => (
        <button type="button" aria-label="删除流水" className="text-muted hover:text-danger" onClick={() => {
          if (window.confirm(`删除 ${t.trade_date} ${t.name} ${t.quantity} 股这笔成交？`)) void api.deleteRealTrade(t.id).then(() => reload())
        }}>
          <Trash2 className="size-4" />
        </button>
      ),
    },
  ]
  const positions: Column<Position>[] = [
    ...positionColumns((c) => navigate(`/stocks/${c}`)),
    {
      key: 'plan', title: '', align: 'right', render: (p) => (
        <Button variant="ghost" onClick={() => setPlan({ code: p.code, name: p.name, stop: String(p.stop_loss ?? ''), target: String(p.target_price ?? '') })}>
          止损止盈
        </Button>
      ),
    },
  ]
  const account = data?.snapshot.account

  return (
    <div>
      <PageHeader
        title="实盘记账"
        description="只记账，不连券商、不下单；持仓会进入盘中止损提醒、组合风险、个股诊断和 AI 问股"
        actions={
          <>
            <Button variant="primary" onClick={() => setTradeOpen(true)}>记一笔</Button>
            <Button onClick={() => fileInput.current?.click()}>导入交割单</Button>
            <Button onClick={() => { setCash(account?.cash_known ? String(account.cash) : ''); setCashOpen(true) }}>设置可用资金</Button>
            <input
              ref={fileInput}
              type="file"
              accept=".csv,.xlsx,.xls,.txt"
              className="hidden"
              aria-label="选择交割单"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) {
                  api.importRealTrades(file)
                    .then((r) => (r.error ? toast.error(r.error) : toast.success(`新增 ${r.added} 笔，重复 ${r.duplicate} 笔，跳过非买卖行 ${r.skipped} 行`)))
                    .then(() => reload())
                    .catch((err) => toast.error(err.message))
                }
                e.target.value = ''
              }}
            />
          </>
        }
      />
      {error && <ErrorBox message={error} onRetry={reload} />}
      {account && (
        <div className="mb-4 grid grid-cols-2 gap-2 md:grid-cols-5">
          <Stat label="总资产" value={fmtMoney(account.total_assets)} />
          <Stat label="可用资金" value={account.cash_known ? fmtMoney(account.cash) : '未设置'} />
          <Stat label="持仓市值" value={fmtMoney(account.market_value)} />
          <Stat label="浮动盈亏" value={<span className={trendClass(account.unrealized_pnl)}>{fmtMoney(account.unrealized_pnl, true)}</span>} />
          <Stat label="已实现盈亏" value={<span className={trendClass(account.realized_pnl)}>{fmtMoney(account.realized_pnl, true)}</span>} />
        </div>
      )}
      <div className="space-y-4">
        {data?.risk && <Card title="组合风险"><RiskPanel risk={data.risk} /></Card>}
        <Card title="持仓" bodyClassName="p-0">
          <DataTable columns={positions} rows={data?.snapshot.positions ?? []} rowKey={(p) => p.code} empty="暂无持仓，记一笔或导入交割单" />
        </Card>
        <Card title="成交流水" bodyClassName="p-0">
          <DataTable columns={tradeColumns} rows={data?.trades ?? []} rowKey={(t) => t.id} maxHeight="50vh" />
        </Card>
      </div>

      <Modal open={tradeOpen} title="记一笔实盘成交" onClose={() => setTradeOpen(false)} footer={<Button variant="primary" onClick={submitTrade}>保存</Button>}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="日期"><Input type="date" value={trade.trade_date} onChange={(e) => setTrade({ ...trade, trade_date: e.target.value })} /></Field>
          <Field label="时间（可空）"><Input value={trade.trade_time} placeholder="10:31" onChange={(e) => setTrade({ ...trade, trade_time: e.target.value })} /></Field>
          <Field label="股票"><Input value={trade.code} placeholder="代码 / 名称 / 拼音" onChange={(e) => setTrade({ ...trade, code: e.target.value })} /></Field>
          <Field label="方向">
            <Select className="w-full" value={trade.side} onChange={(e) => setTrade({ ...trade, side: e.target.value })}>
              <option value="buy">买入</option>
              <option value="sell">卖出</option>
            </Select>
          </Field>
          <Field label="成交价"><Input type="number" step="0.001" value={trade.price} onChange={(e) => setTrade({ ...trade, price: e.target.value })} /></Field>
          <Field label="数量（股）"><Input type="number" step="100" value={trade.quantity} onChange={(e) => setTrade({ ...trade, quantity: e.target.value })} /></Field>
          <Field label="费用合计"><Input type="number" step="0.01" value={trade.fee} onChange={(e) => setTrade({ ...trade, fee: e.target.value })} /></Field>
          <Field label="备注"><Input value={trade.note} onChange={(e) => setTrade({ ...trade, note: e.target.value })} /></Field>
        </div>
      </Modal>
      <Modal
        open={cashOpen}
        title="设置可用资金"
        onClose={() => setCashOpen(false)}
        footer={<Button variant="primary" onClick={() => api.setRealCash(Number(cash)).then(() => { setCashOpen(false); void reload() })}>保存</Button>}
      >
        <Field label="券商账户当前的可用资金（元）" hint="之后发生的成交会自动增减">
          <Input type="number" value={cash} onChange={(e) => setCash(e.target.value)} />
        </Field>
      </Modal>
      <Modal
        open={plan != null}
        title={`止损止盈：${plan?.name ?? ''}`}
        onClose={() => setPlan(null)}
        footer={
          <Button variant="primary" onClick={() => plan && api.setRealPlan(plan.code, Number(plan.stop) || null, Number(plan.target) || null).then(() => { setPlan(null); void reload() })}>
            保存
          </Button>
        }
      >
        {plan && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="止损价（0 表示按风控比例）"><Input type="number" step="0.01" value={plan.stop} onChange={(e) => setPlan({ ...plan, stop: e.target.value })} /></Field>
            <Field label="目标价（0 表示按风控比例）"><Input type="number" step="0.01" value={plan.target} onChange={(e) => setPlan({ ...plan, target: e.target.value })} /></Field>
          </div>
        )}
      </Modal>
    </div>
  )
}
