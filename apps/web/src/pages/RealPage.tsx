// 实盘记账：手动记一笔、导入交割单、设置可用资金和止损止盈；只记账，不连券商、不下单
import { Trash2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { Position, RealCorporateAction, RealImportPreview, RealTrade } from '@/api/types'
import { actionBody, CorporateActionForm, emptyActionForm, type ActionForm } from '@/components/CorporateActionForm'
import { DataTable, type Column } from '@/components/DataTable'
import { RiskPanel } from '@/components/RiskPanel'
import { Button, Card, ErrorBox, Field, Input, Modal, PageHeader, Select, Stat } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'
import { fmtMoney, fmtNum, trendClass } from '@/utils/format'
import { positionColumns } from './TradingPage'

const today = () => new Date().toISOString().slice(0, 10)
const EMPTY_TRADE = { trade_date: today(), trade_time: '', code: '', side: 'buy', price: '', quantity: '100', fee: '0', note: '' }

export function RealPage() {
  const t = useT()
  const navigate = useNavigate()
  const { data, error, reload } = useApi(api.real)
  const [tradeOpen, setTradeOpen] = useState(false)
  const [trade, setTrade] = useState(EMPTY_TRADE)
  const [cashOpen, setCashOpen] = useState(false)
  const [cash, setCash] = useState('')
  const [plan, setPlan] = useState<{ code: string; name: string; stop: string; target: string } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const actionsApi = useApi(api.realActions)
  const [actionOpen, setActionOpen] = useState(false)
  const [actionForm, setActionForm] = useState<ActionForm>(emptyActionForm)
  const [importPreview, setImportPreview] = useState<{ file: File; preview: RealImportPreview } | null>(null)

  const submitAction = async () => {
    try {
      await api.addRealAction(actionBody(actionForm))
      toast.success(t('已记录'))
      setActionOpen(false)
      setActionForm(emptyActionForm())
      void reload()
      void actionsApi.reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    }
  }
  const startPreview = (file: File) => {
    api.previewRealImport(file)
      .then((preview) => (preview.error ? toast.error(preview.error) : setImportPreview({ file, preview })))
      .catch((err) => toast.error(err.message))
  }
  const confirmImport = () => {
    if (!importPreview) return
    const { file } = importPreview
    setImportPreview(null)
    api.importRealTrades(file)
      .then((r) => (r.error ? toast.error(r.error) : toast.success(t('导入 {a} 笔成交、{b} 条分红送转，重复 {c} 条，跳过 {d} 行', { a: r.added, b: r.actions_added ?? 0, c: r.duplicate, d: r.skipped }))))
      .then(() => { void reload(); void actionsApi.reload() })
      .catch((err) => toast.error(err.message))
  }

  const submitTrade = async () => {
    try {
      await api.addRealTrade({ ...trade, price: Number(trade.price), quantity: Number(trade.quantity), fee: Number(trade.fee || 0) })
      toast.success(t('已记录'))
      setTradeOpen(false)
      setTrade({ ...EMPTY_TRADE, trade_date: today() })
      void reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    }
  }

  const tradeColumns: Column<RealTrade>[] = [
    { key: 'date', title: t('日期'), render: (x) => <span className="num">{x.trade_date} <span className="text-xs text-muted">{x.trade_time}</span></span> },
    { key: 'stock', title: t('股票'), render: (x) => <>{x.name} <span className="num text-xs text-muted">{x.code}</span></> },
    { key: 'side', title: t('方向'), render: (x) => <span className={x.side === 'buy' ? 'text-up' : 'text-down'}>{x.side === 'buy' ? t('买入') : t('卖出')}</span> },
    { key: 'price', title: t('成交价'), align: 'right', render: (x) => <span className="num">{fmtNum(x.price, 3)}</span> },
    { key: 'qty', title: t('数量'), align: 'right', render: (x) => <span className="num">{x.quantity}</span> },
    { key: 'fee', title: t('费用'), align: 'right', render: (x) => <span className="num">{fmtNum(x.fee)}</span> },
    { key: 'source', title: t('来源'), render: (x) => <span className="text-xs text-muted">{x.source === 'import' ? t('导入') : t('手动')}</span> },
    { key: 'note', title: t('备注'), className: 'text-xs text-muted', render: (x) => x.note },
    {
      key: 'del', title: '', align: 'right', render: (x) => (
        <button type="button" aria-label={t('删除流水')} className="text-muted hover:text-danger" onClick={() => {
          if (window.confirm(t('删除 {date} {name} {qty} 股这笔成交？', { date: x.trade_date, name: x.name, qty: x.quantity }))) void api.deleteRealTrade(x.id).then(() => reload())
        }}>
          <Trash2 className="size-4" />
        </button>
      ),
    },
  ]
  const actionColumns: Column<RealCorporateAction>[] = [
    { key: 'date', title: t('除权日'), render: (a) => <span className="num">{a.ex_date}</span> },
    { key: 'stock', title: t('股票'), render: (a) => <>{a.name} <span className="num text-xs text-muted">{a.code}</span></> },
    { key: 'type', title: t('类型'), render: (a) => t(a.action_label) },
    {
      key: 'value', title: t('金额 / 股数'), align: 'right',
      render: (a) => <span className="num">{a.action === 'bonus' ? t('{n} 股', { n: a.shares }) : `${a.action === 'tax' ? '-' : '+'}${t('{v} 元', { v: fmtNum(a.cash) })}`}</span>,
    },
    { key: 'source', title: t('来源'), render: (a) => <span className="text-xs text-muted">{a.source === 'import' ? t('导入') : t('手动')}</span> },
    { key: 'note', title: t('备注'), className: 'text-xs text-muted', render: (a) => a.note },
    {
      key: 'del', title: '', align: 'right', render: (a) => (
        <button type="button" aria-label={t('删除分红送转')} className="text-muted hover:text-danger" onClick={() => {
          if (window.confirm(t('删除 {date} {name} 这条分红送转记录？', { date: a.ex_date, name: a.name }))) void api.deleteRealAction(a.id).then(() => { void reload(); void actionsApi.reload() })
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
          {t('止损止盈')}
        </Button>
      ),
    },
  ]
  const account = data?.snapshot.account

  return (
    <div>
      <PageHeader
        title={t('实盘记账')}
        description={t('只记账，不连券商、不下单；持仓会进入盘中止损提醒、组合风险、个股诊断和 AI 问股')}
        actions={
          <>
            <Button variant="primary" onClick={() => setTradeOpen(true)}>{t('记一笔')}</Button>
            <Button onClick={() => setActionOpen(true)}>{t('记一笔分红送转')}</Button>
            <Button onClick={() => fileInput.current?.click()}>{t('导入交割单')}</Button>
            <Button onClick={() => { setCash(account?.cash_known ? String(account.cash) : ''); setCashOpen(true) }}>{t('设置可用资金')}</Button>
            <input
              ref={fileInput}
              type="file"
              accept=".csv,.xlsx,.xls,.txt"
              className="hidden"
              aria-label={t('选择交割单')}
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) startPreview(file)
                e.target.value = ''
              }}
            />
          </>
        }
      />
      {error && <ErrorBox message={error} onRetry={reload} />}
      {account && (
        <div className="mb-4 grid grid-cols-2 gap-2 md:grid-cols-5">
          <Stat label={t('总资产')} value={fmtMoney(account.total_assets)} />
          <Stat label={t('可用资金')} value={account.cash_known ? fmtMoney(account.cash) : t('未设置')} />
          <Stat label={t('持仓市值')} value={fmtMoney(account.market_value)} />
          <Stat label={t('浮动盈亏')} value={<span className={trendClass(account.unrealized_pnl)}>{fmtMoney(account.unrealized_pnl, true)}</span>} />
          <Stat label={t('已实现盈亏')} value={<span className={trendClass(account.realized_pnl)}>{fmtMoney(account.realized_pnl, true)}</span>} />
        </div>
      )}
      <div className="space-y-4">
        {data?.risk && <Card title={t('组合风险')}><RiskPanel risk={data.risk} /></Card>}
        <Card title={t('持仓')} bodyClassName="p-0">
          <DataTable columns={positions} rows={data?.snapshot.positions ?? []} rowKey={(p) => p.code} empty={t('暂无持仓，记一笔或导入交割单')} />
        </Card>
        <Card title={t('分红送转')} bodyClassName="p-0">
          <DataTable columns={actionColumns} rows={actionsApi.data ?? []} rowKey={(a) => a.id} maxHeight="40vh" empty={t('暂无分红送转记录')} />
        </Card>
        <Card title={t('成交流水')} bodyClassName="p-0">
          <DataTable columns={tradeColumns} rows={data?.trades ?? []} rowKey={(x) => x.id} maxHeight="50vh" />
        </Card>
      </div>

      <Modal open={actionOpen} title={t('记一笔分红送转')} onClose={() => setActionOpen(false)} footer={<Button variant="primary" onClick={submitAction}>{t('保存')}</Button>}>
        <CorporateActionForm value={actionForm} onChange={setActionForm} />
      </Modal>
      <Modal
        open={importPreview != null}
        title={t('导入交割单预览')}
        wide
        onClose={() => setImportPreview(null)}
        footer={
          <>
            <Button onClick={() => setImportPreview(null)}>{t('取消')}</Button>
            <Button variant="primary" onClick={confirmImport} disabled={!importPreview || importPreview.preview.new_trades + importPreview.preview.new_actions === 0}>{t('确认导入')}</Button>
          </>
        }
      >
        {importPreview && <ImportPreviewView preview={importPreview.preview} />}
      </Modal>
      <Modal open={tradeOpen} title={t('记一笔实盘成交')} onClose={() => setTradeOpen(false)} footer={<Button variant="primary" onClick={submitTrade}>{t('保存')}</Button>}>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('日期')}><Input type="date" value={trade.trade_date} onChange={(e) => setTrade({ ...trade, trade_date: e.target.value })} /></Field>
          <Field label={t('时间（可空）')}><Input value={trade.trade_time} placeholder="10:31" onChange={(e) => setTrade({ ...trade, trade_time: e.target.value })} /></Field>
          <Field label={t('股票')}><Input value={trade.code} placeholder={t('代码 / 名称 / 拼音')} onChange={(e) => setTrade({ ...trade, code: e.target.value })} /></Field>
          <Field label={t('方向')}>
            <Select className="w-full" value={trade.side} onChange={(e) => setTrade({ ...trade, side: e.target.value })}>
              <option value="buy">{t('买入')}</option>
              <option value="sell">{t('卖出')}</option>
            </Select>
          </Field>
          <Field label={t('成交价')}><Input type="number" step="0.001" value={trade.price} onChange={(e) => setTrade({ ...trade, price: e.target.value })} /></Field>
          <Field label={t('数量（股）')}><Input type="number" step="100" value={trade.quantity} onChange={(e) => setTrade({ ...trade, quantity: e.target.value })} /></Field>
          <Field label={t('费用合计')}><Input type="number" step="0.01" value={trade.fee} onChange={(e) => setTrade({ ...trade, fee: e.target.value })} /></Field>
          <Field label={t('备注')}><Input value={trade.note} onChange={(e) => setTrade({ ...trade, note: e.target.value })} /></Field>
        </div>
      </Modal>
      <Modal
        open={cashOpen}
        title={t('设置可用资金')}
        onClose={() => setCashOpen(false)}
        footer={<Button variant="primary" onClick={() => api.setRealCash(Number(cash)).then(() => { setCashOpen(false); void reload() })}>{t('保存')}</Button>}
      >
        <Field label={t('券商账户当前的可用资金（元）')} hint={t('之后发生的成交会自动增减')}>
          <Input type="number" value={cash} onChange={(e) => setCash(e.target.value)} />
        </Field>
      </Modal>
      <Modal
        open={plan != null}
        title={t('止损止盈：{name}', { name: plan?.name ?? '' })}
        onClose={() => setPlan(null)}
        footer={
          <Button variant="primary" onClick={() => plan && api.setRealPlan(plan.code, Number(plan.stop) || null, Number(plan.target) || null).then(() => { setPlan(null); void reload() })}>
            {t('保存')}
          </Button>
        }
      >
        {plan && (
          <div className="grid grid-cols-2 gap-3">
            <Field label={t('止损价（0 表示按风控比例）')}><Input type="number" step="0.01" value={plan.stop} onChange={(e) => setPlan({ ...plan, stop: e.target.value })} /></Field>
            <Field label={t('目标价（0 表示按风控比例）')}><Input type="number" step="0.01" value={plan.target} onChange={(e) => setPlan({ ...plan, target: e.target.value })} /></Field>
          </div>
        )}
      </Modal>
    </div>
  )
}

function ImportPreviewView({ preview: p }: { preview: RealImportPreview }) {
  const t = useT()
  return (
    <div className="space-y-3 text-sm">
      <div>{t('新增成交 {a} 笔，新增分红送转 {b} 条；重复 {c} 条，跳过 {d} 行', { a: p.new_trades, b: p.new_actions, c: p.duplicates, d: p.skipped })}</div>
      {p.warnings.map((w) => <div key={w} className="text-xs text-warn">{w}</div>)}
      {p.actions.length > 0 && (
        <ul className="space-y-1 text-xs">
          {p.actions.map((a, i) => (
            <li key={i} className={a.duplicate ? 'text-muted' : ''}>
              {a.ex_date} {a.name || a.code} {t(a.action_label ?? a.action)} {a.action === 'bonus' ? t('{n} 股', { n: a.shares }) : t('{v} 元', { v: fmtNum(a.cash) })}{a.duplicate ? t('（重复）') : ''}
            </li>
          ))}
        </ul>
      )}
      {p.trades.length > 0 && (
        <ul className="max-h-64 space-y-1 overflow-auto text-xs">
          {p.trades.map((x, i) => (
            <li key={i} className={x.duplicate ? 'text-muted' : ''}>
              <span className="num">{x.trade_date} {x.trade_time ?? ''}</span> {x.name || x.code} {x.side === 'buy' ? t('买入') : t('卖出')} {t('{n} 股', { n: x.quantity })} @ {fmtNum(x.price, 3)}{x.duplicate ? t('（重复）') : ''}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
