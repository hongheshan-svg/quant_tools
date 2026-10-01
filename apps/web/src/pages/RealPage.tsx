// 实盘记账：手动记一笔、导入交割单、设置可用资金和止损止盈；只记账，不连券商、不下单
import { Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { Position, RealAccount, RealCashFlow, RealCorporateAction, RealImportPreview, RealTrade } from '@/api/types'
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
const ACCOUNT_KEY = 'quant-real-account'
const DEFAULT_ACCOUNT = '默认'
const EMPTY_TRADE = { trade_date: today(), trade_time: '', code: '', side: 'buy', price: '', quantity: '100', fee: '0', note: '' }

function loadAccount(): string {
  try { return localStorage.getItem(ACCOUNT_KEY) ?? '' } catch { return '' }
}

export function RealPage() {
  const t = useT()
  const navigate = useNavigate()
  const [account, setAccountState] = useState(loadAccount)
  const accountsApi = useApi(api.realAccounts)
  const accountList: RealAccount[] = Array.isArray(accountsApi.data) ? accountsApi.data : []
  const accountNames = accountList.length > 0 ? accountList.map((a) => a.name) : [DEFAULT_ACCOUNT]
  const multi = accountNames.length > 1
  const setAccount = (name: string) => {
    setAccountState(name)
    try { localStorage.setItem(ACCOUNT_KEY, name) } catch { /* 忽略 */ }
  }
  // 保存的账户已被删除或改名时回到全部账户
  useEffect(() => {
    if (accountsApi.data && account && !accountNames.includes(account)) setAccount('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountsApi.data])
  // 录入时默认记入的账户：当前选中的账户，全部账户视图下是默认账户
  const targetAccount = account || DEFAULT_ACCOUNT
  const showAccounts = multi && !account
  const { data, error, reload } = useApi(() => api.real(account), [account])
  const [manageOpen, setManageOpen] = useState(false)
  const [formAccount, setFormAccount] = useState(DEFAULT_ACCOUNT)
  const [tradeOpen, setTradeOpen] = useState(false)
  const [trade, setTrade] = useState(EMPTY_TRADE)
  const [cashOpen, setCashOpen] = useState(false)
  const [cash, setCash] = useState('')
  const [plan, setPlan] = useState<{ code: string; name: string; stop: string; target: string; accounts: string[]; account: string } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const actionsApi = useApi(() => api.realActions(account), [account])
  const flowsApi = useApi(() => api.realCashFlows(account), [account])
  const [flowOpen, setFlowOpen] = useState(false)
  const [flow, setFlow] = useState<{ flow_date: string; direction: 'in' | 'out'; amount: string; note: string }>({ flow_date: today(), direction: 'in', amount: '', note: '' })
  const [actionOpen, setActionOpen] = useState(false)
  const [actionForm, setActionForm] = useState<ActionForm>(emptyActionForm)
  const [importPreview, setImportPreview] = useState<{ file: File; preview: RealImportPreview; account: string } | null>(null)

  const submitAction = async () => {
    try {
      await api.addRealAction(actionBody({ ...actionForm, account: multi ? actionForm.account || DEFAULT_ACCOUNT : account }))
      toast.success(t('已记录'))
      setActionOpen(false)
      setActionForm(emptyActionForm(targetAccount))
      void reload()
      void actionsApi.reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    }
  }
  const startPreview = (file: File, target = targetAccount) => {
    api.previewRealImport(file, target === DEFAULT_ACCOUNT ? '' : target)
      .then((preview) => (preview.error ? toast.error(preview.error) : setImportPreview({ file, preview, account: target })))
      .catch((err) => toast.error(err.message))
  }
  const confirmImport = () => {
    if (!importPreview) return
    const { file, account: target } = importPreview
    setImportPreview(null)
    api.importRealTrades(file, target === DEFAULT_ACCOUNT ? '' : target)
      .then((r) => (r.error ? toast.error(r.error) : toast.success(t('导入 {a} 笔成交、{b} 条分红送转，重复 {c} 条，跳过 {d} 行', { a: r.added, b: r.actions_added ?? 0, c: r.duplicate, d: r.skipped }))))
      .then(() => { void reload(); void actionsApi.reload() })
      .catch((err) => toast.error(err.message))
  }

  const submitFlow = async () => {
    try {
      await api.addRealCashFlow({ ...flow, amount: Number(flow.amount), account: formAccount === DEFAULT_ACCOUNT ? '' : formAccount })
      toast.success(t('已记录'))
      setFlowOpen(false)
      setFlow({ flow_date: today(), direction: 'in', amount: '', note: '' })
      void reload()
      void flowsApi.reload()
      void accountsApi.reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    }
  }
  const submitTrade = async () => {
    try {
      await api.addRealTrade({ ...trade, account: formAccount === DEFAULT_ACCOUNT ? '' : formAccount, price: Number(trade.price), quantity: Number(trade.quantity), fee: Number(trade.fee || 0) })
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
  const base = positionColumns((c) => navigate(`/stocks/${c}`))
  const positions: Column<Position>[] = [
    base[0],
    ...(showAccounts ? [{ key: 'account', title: t('账户'), render: (p: Position) => <span className="text-xs">{(p.accounts ?? []).join('、')}</span> }] : []),
    ...base.slice(1),
    {
      key: 'plan', title: '', align: 'right', render: (p) => (
        <Button variant="ghost" onClick={() => {
          const owners = p.accounts && p.accounts.length > 0 ? p.accounts : [targetAccount]
          setPlan({ code: p.code, name: p.name, stop: String(p.stop_loss ?? ''), target: String(p.target_price ?? ''), accounts: owners, account: owners[0] })
        }}>
          {t('止损止盈')}
        </Button>
      ),
    },
  ]
  const flowColumns: Column<RealCashFlow>[] = [
    { key: 'date', title: t('日期'), render: (f) => <span className="num">{f.flow_date}</span> },
    { key: 'dir', title: t('方向'), render: (f) => <span className={f.direction === 'in' ? 'text-up' : 'text-down'}>{t(f.direction_label)}</span> },
    { key: 'amount', title: t('金额'), align: 'right', render: (f) => <span className="num">{`${f.direction === 'in' ? '+' : '-'}${fmtNum(f.amount)}`}</span> },
    ...(showAccounts ? [{ key: 'account', title: t('账户'), render: (f: RealCashFlow) => <span className="text-xs text-muted">{f.account}</span> }] : []),
    { key: 'note', title: t('备注'), className: 'text-xs text-muted', render: (f) => f.note },
    {
      key: 'del', title: '', align: 'right', render: (f) => (
        <button type="button" aria-label={t('删除出入金')} className="text-muted hover:text-danger" onClick={() => {
          if (window.confirm(t('删除 {date} 这笔{dir}？', { date: f.flow_date, dir: t(f.direction_label) }))) void api.deleteRealCashFlow(f.id).then(() => { void reload(); void flowsApi.reload(); void accountsApi.reload() })
        }}>
          <Trash2 className="size-4" />
        </button>
      ),
    },
  ]
  const summary = data?.snapshot.account
  const hasFlows = (flowsApi.data ?? []).length > 0
  // 某个账户当前的可用资金（没设置过为空）；选中单个账户时用持仓快照里的数
  const cashOf = (name: string) => {
    if (account === name && summary) return summary.cash_known ? String(summary.cash) : ''
    const found = accountList.find((a) => a.name === name)
    if (found) return found.cash != null ? String(found.cash) : ''
    return !multi && summary?.cash_known ? String(summary.cash) : ''
  }

  return (
    <div>
      <PageHeader
        title={t('实盘记账')}
        description={t('只记账，不连券商、不下单；持仓会进入盘中止损提醒、组合风险、个股诊断和 AI 问股')}
        actions={
          <>
            {multi && (
              <Select aria-label={t('账户')} value={account} onChange={(e) => setAccount(e.target.value)}>
                <option value="">{t('全部账户')}</option>
                {accountNames.map((n) => <option key={n} value={n}>{n}</option>)}
              </Select>
            )}
            <Button variant="primary" onClick={() => { setFormAccount(targetAccount); setTradeOpen(true) }}>{t('记一笔')}</Button>
            <Button onClick={() => { setActionForm((f) => ({ ...f, account: targetAccount })); setActionOpen(true) }}>{t('记一笔分红送转')}</Button>
            <Button onClick={() => { setFormAccount(targetAccount); setFlowOpen(true) }}>{t('记一笔出入金')}</Button>
            <Button onClick={() => fileInput.current?.click()}>{t('导入交割单')}</Button>
            <Button onClick={() => { setFormAccount(targetAccount); setCash(cashOf(targetAccount)); setCashOpen(true) }}>{t('设置可用资金')}</Button>
            <Button onClick={() => setManageOpen(true)}>{t('管理账户')}</Button>
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
      {summary && (
        <div className={`mb-4 grid grid-cols-2 gap-2 ${hasFlows ? 'md:grid-cols-6' : 'md:grid-cols-5'}`}>
          <Stat label={t('总资产')} value={fmtMoney(summary.total_assets)} />
          <Stat label={t('可用资金')} value={summary.cash_known ? fmtMoney(summary.cash) : t('未设置')} />
          <Stat label={t('持仓市值')} value={fmtMoney(summary.market_value)} />
          <Stat label={t('浮动盈亏')} value={<span className={trendClass(summary.unrealized_pnl)}>{fmtMoney(summary.unrealized_pnl, true)}</span>} />
          <Stat label={t('已实现盈亏')} value={<span className={trendClass(summary.realized_pnl)}>{fmtMoney(summary.realized_pnl, true)}</span>} />
          {hasFlows && (
            <Stat
              label={summary.ledger_mode ? t('累计收益') : t('净入金')}
              value={summary.ledger_mode && summary.total_return != null
                ? <span className={trendClass(summary.total_return)}>{fmtMoney(summary.total_return, true)}</span>
                : fmtMoney(summary.net_deposit ?? 0)}
              sub={summary.ledger_mode ? t('净入金 {v}', { v: fmtMoney(summary.net_deposit ?? 0) }) : undefined}
            />
          )}
        </div>
      )}
      <div className="space-y-4">
        {data?.risk && <Card title={t('组合风险')}><RiskPanel risk={data.risk} /></Card>}
        <Card title={t('持仓')} bodyClassName="p-0">
          <DataTable columns={positions} rows={data?.snapshot.positions ?? []} rowKey={(p) => p.code} empty={t('暂无持仓，记一笔或导入交割单')} />
        </Card>
        <Card title={t('出入金')} bodyClassName="p-0">
          <DataTable columns={flowColumns} rows={flowsApi.data ?? []} rowKey={(f) => f.id} maxHeight="30vh" empty={t('暂无出入金记录；只记出入金和成交、不设置可用资金时，按全部流水算出可用资金和累计收益')} />
        </Card>
        <Card title={t('分红送转')} bodyClassName="p-0">
          <DataTable columns={actionColumns} rows={actionsApi.data ?? []} rowKey={(a) => a.id} maxHeight="40vh" empty={t('暂无分红送转记录')} />
        </Card>
        <Card title={t('成交流水')} bodyClassName="p-0">
          <DataTable columns={tradeColumns} rows={data?.trades ?? []} rowKey={(x) => x.id} maxHeight="50vh" />
        </Card>
      </div>

      <Modal open={actionOpen} title={t('记一笔分红送转')} onClose={() => setActionOpen(false)} footer={<Button variant="primary" onClick={submitAction}>{t('保存')}</Button>}>
        <CorporateActionForm value={actionForm} onChange={setActionForm} accounts={multi ? accountNames : undefined} />
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
        {importPreview && multi && (
          <div className="mb-3 max-w-xs">
            <Field label={t('导入到账户')}>
              <Select className="w-full" value={importPreview.account} onChange={(e) => startPreview(importPreview.file, e.target.value)}>
                {accountNames.map((n) => <option key={n} value={n}>{n}</option>)}
              </Select>
            </Field>
          </div>
        )}
        {importPreview && <ImportPreviewView preview={importPreview.preview} />}
      </Modal>
      <Modal open={flowOpen} title={t('记一笔出入金')} onClose={() => setFlowOpen(false)} footer={<Button variant="primary" onClick={submitFlow}>{t('保存')}</Button>}>
        <div className="grid grid-cols-2 gap-3">
          {multi && (
            <Field label={t('账户')}>
              <Select className="w-full" value={formAccount} onChange={(e) => setFormAccount(e.target.value)}>
                {accountNames.map((n) => <option key={n} value={n}>{n}</option>)}
              </Select>
            </Field>
          )}
          <Field label={t('日期')}><Input type="date" value={flow.flow_date} onChange={(e) => setFlow({ ...flow, flow_date: e.target.value })} /></Field>
          <Field label={t('方向')}>
            <Select className="w-full" value={flow.direction} onChange={(e) => setFlow({ ...flow, direction: e.target.value as 'in' | 'out' })}>
              <option value="in">{t('入金（银行转证券）')}</option>
              <option value="out">{t('出金（证券转银行）')}</option>
            </Select>
          </Field>
          <Field label={t('金额（元）')}><Input type="number" step="0.01" value={flow.amount} onChange={(e) => setFlow({ ...flow, amount: e.target.value })} /></Field>
          <Field label={t('备注')}><Input value={flow.note} onChange={(e) => setFlow({ ...flow, note: e.target.value })} /></Field>
        </div>
      </Modal>
      <Modal open={tradeOpen} title={t('记一笔实盘成交')} onClose={() => setTradeOpen(false)} footer={<Button variant="primary" onClick={submitTrade}>{t('保存')}</Button>}>
        <div className="grid grid-cols-2 gap-3">
          {multi && (
            <Field label={t('账户')}>
              <Select className="w-full" value={formAccount} onChange={(e) => setFormAccount(e.target.value)}>
                {accountNames.map((n) => <option key={n} value={n}>{n}</option>)}
              </Select>
            </Field>
          )}
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
        footer={<Button variant="primary" onClick={() => api.setRealCash(Number(cash), formAccount === DEFAULT_ACCOUNT ? '' : formAccount).then(() => { setCashOpen(false); void reload(); void accountsApi.reload() })}>{t('保存')}</Button>}
      >
        {multi && (
          <Field label={t('账户')}>
            <Select className="w-full" value={formAccount} onChange={(e) => { setFormAccount(e.target.value); setCash(cashOf(e.target.value)) }}>
              {accountNames.map((n) => <option key={n} value={n}>{n}</option>)}
            </Select>
          </Field>
        )}
        <Field label={t('券商账户当前的可用资金（元）')} hint={t('之后发生的成交和出入金会自动增减；设置之前日期的出入金视为已包含在内')}>
          <Input type="number" value={cash} onChange={(e) => setCash(e.target.value)} />
        </Field>
      </Modal>
      <Modal
        open={plan != null}
        title={t('止损止盈：{name}', { name: plan?.name ?? '' })}
        onClose={() => setPlan(null)}
        footer={
          <Button variant="primary" onClick={() => plan && api.setRealPlan(plan.code, Number(plan.stop) || null, Number(plan.target) || null, plan.account === DEFAULT_ACCOUNT ? '' : plan.account).then(() => { setPlan(null); void reload() })}>
            {t('保存')}
          </Button>
        }
      >
        {plan && (
          <div className="grid grid-cols-2 gap-3">
            {plan.accounts.length > 1 && (
              <Field label={t('账户')}>
                <Select className="w-full" value={plan.account} onChange={(e) => setPlan({ ...plan, account: e.target.value })}>
                  {plan.accounts.map((n) => <option key={n} value={n}>{n}</option>)}
                </Select>
              </Field>
            )}
            <Field label={t('止损价（0 表示按风控比例）')}><Input type="number" step="0.01" value={plan.stop} onChange={(e) => setPlan({ ...plan, stop: e.target.value })} /></Field>
            <Field label={t('目标价（0 表示按风控比例）')}><Input type="number" step="0.01" value={plan.target} onChange={(e) => setPlan({ ...plan, target: e.target.value })} /></Field>
          </div>
        )}
      </Modal>
      <AccountManager
        open={manageOpen}
        accounts={accountList}
        onClose={() => setManageOpen(false)}
        onChanged={(renamed) => {
          if (renamed && renamed.from === account) setAccount(renamed.to)
          void accountsApi.reload()
          void reload()
          void actionsApi.reload()
          void flowsApi.reload()
        }}
      />
    </div>
  )
}

/** 管理账户：新增、改名、删除（默认账户不能改名和删除） */
function AccountManager({ open, accounts, onClose, onChanged }: {
  open: boolean
  accounts: RealAccount[]
  onClose: () => void
  onChanged: (renamed?: { from: string; to: string }) => void
}) {
  const t = useT()
  const [name, setName] = useState('')
  const [broker, setBroker] = useState('')
  const [editing, setEditing] = useState<{ from: string; name: string; broker: string; note: string } | null>(null)
  const fail = (e: unknown) => toast.error(e instanceof Error ? e.message : String(e))
  const add = () => {
    api.addRealAccount({ name: name.trim(), broker: broker.trim() })
      .then(() => { setName(''); setBroker(''); toast.success(t('已新增账户')); onChanged() })
      .catch(fail)
  }
  const save = () => {
    if (!editing) return
    api.updateRealAccount(editing.from, { name: editing.name.trim(), broker: editing.broker.trim(), note: editing.note })
      .then(() => { const e = editing; setEditing(null); onChanged(e.from !== e.name.trim() ? { from: e.from, to: e.name.trim() } : undefined) })
      .catch(fail)
  }
  const remove = (a: RealAccount) => {
    if (!window.confirm(t('删除账户「{name}」？', { name: a.name }))) return
    api.deleteRealAccount(a.name).then(() => { toast.success(t('已删除账户')); onChanged({ from: a.name, to: '' }) }).catch(fail)
  }
  const list = accounts.length > 0 ? accounts : [{ name: DEFAULT_ACCOUNT, broker: '', note: '', trades: 0, positions: 0, market_value: 0, cash: null }]
  return (
    <Modal open={open} title={t('管理账户')} onClose={onClose}>
      <ul className="mb-4 space-y-2 text-sm">
        {list.map((a) => (
          <li key={a.name} className="flex items-center justify-between gap-2 rounded-md border border-line px-3 py-2">
            {editing?.from === a.name ? (
              <div className="flex flex-1 flex-wrap items-center gap-2">
                <Input aria-label={t('账户名称')} value={editing.name} maxLength={30} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
                <Input aria-label={t('券商（可空）')} value={editing.broker} placeholder={t('券商（可空）')} maxLength={30} onChange={(e) => setEditing({ ...editing, broker: e.target.value })} />
                <Button variant="primary" onClick={save}>{t('保存')}</Button>
                <Button onClick={() => setEditing(null)}>{t('取消')}</Button>
              </div>
            ) : (
              <>
                <div>
                  <span className="font-medium">{a.name}</span>
                  {a.broker && <span className="ml-2 text-xs text-muted">{a.broker}</span>}
                  <div className="text-xs text-muted">{t('{n} 笔成交，{m} 只持仓', { n: a.trades, m: a.positions })}</div>
                </div>
                {a.name !== DEFAULT_ACCOUNT && (
                  <div className="flex gap-1">
                    <Button variant="ghost" onClick={() => setEditing({ from: a.name, name: a.name, broker: a.broker, note: a.note })}>{t('改名')}</Button>
                    <Button variant="ghost" onClick={() => remove(a)}>{t('删除')}</Button>
                  </div>
                )}
              </>
            )}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-end gap-2">
        <Field label={t('账户名称')}><Input value={name} maxLength={30} placeholder={t('如：招商证券')} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label={t('券商（可空）')}><Input value={broker} maxLength={30} onChange={(e) => setBroker(e.target.value)} /></Field>
        <Button variant="primary" disabled={!name.trim()} onClick={add}>{t('新增账户')}</Button>
      </div>
    </Modal>
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
