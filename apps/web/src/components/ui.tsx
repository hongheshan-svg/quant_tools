// 基础界面组件：按钮、卡片、标签、输入框、页签、弹窗、空状态等
import { Loader2, X } from 'lucide-react'
import { createPortal } from 'react-dom'
import { useEffect, useId, useSyncExternalStore, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { useT } from '@/i18n'
import { cn } from '@/utils/cn'
import { fmtPct, trendClass } from '@/utils/format'

type Variant = 'primary' | 'default' | 'ghost' | 'danger'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent-strong text-white hover:brightness-110 border-transparent',
  default: 'bg-panel-2 text-text hover:border-accent border-line',
  ghost: 'bg-transparent text-muted hover:text-text border-transparent',
  danger: 'bg-transparent text-danger hover:bg-danger/10 border-danger/40',
}

export function Button({
  variant = 'default',
  loading,
  className,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean }) {
  return (
    <button
      type="button"
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50',
        VARIANTS[variant],
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && <Loader2 className="size-3.5 animate-spin" />}
      {children}
    </button>
  )
}

export function Card({ title, actions, children, className, bodyClassName }: {
  title?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  const t = useT()
  return (
    <section className={cn('rounded-lg border border-line bg-panel', className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold text-accent">{typeof title === 'string' ? t(title) : title}</h2>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn('p-4', bodyClassName)}>{children}</div>
    </section>
  )
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  const t = useT()
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-lg font-semibold">{t(title)}</h1>
        {description && <p className="mt-0.5 text-xs text-muted">{typeof description === 'string' ? t(description) : description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Badge({ children, tone = 'default', className }: { children: ReactNode; tone?: 'default' | 'up' | 'down' | 'warn' | 'accent'; className?: string }) {
  const tones = {
    default: 'border-line text-muted',
    up: 'border-up/40 text-up bg-up/10',
    down: 'border-down/40 text-down bg-down/10',
    warn: 'border-warn/40 text-warn bg-warn/10',
    accent: 'border-accent/40 text-accent bg-accent/10',
  }
  return <span className={cn('inline-flex items-center rounded border px-1.5 py-0.5 text-xs whitespace-nowrap', tones[tone], className)}>{children}</span>
}

export function Pct({ value, digits = 2, signed = true }: { value: unknown; digits?: number; signed?: boolean }) {
  return <span className={cn('num', signed ? trendClass(value) : '')}>{fmtPct(value, digits, signed)}</span>
}

export function Stat({ label, value, sub, className }: { label: string; value: ReactNode; sub?: ReactNode; className?: string }) {
  const t = useT()
  return (
    <div className={cn('rounded-md border border-line bg-panel-2 px-3 py-2', className)}>
      <div className="text-xs text-muted">{t(label)}</div>
      <div className="num mt-0.5 text-base font-semibold">{value}</div>
      {sub && <div className="mt-0.5 text-xs">{sub}</div>}
    </div>
  )
}

const inputClass = 'w-full rounded-md border border-line bg-bg px-2.5 py-1.5 text-sm text-text outline-none placeholder:text-muted focus:border-accent'

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(inputClass, className)} {...rest} />
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(inputClass, 'min-h-24', className)} {...rest} />
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(inputClass, 'w-auto', className)} {...rest}>
      {children}
    </select>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  const t = useT()
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-muted">{t(label)}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{t(hint)}</span>}
    </label>
  )
}

export function Tabs<K extends string>({ tabs, value, onChange }: { tabs: { key: K; label: ReactNode }[]; value: K; onChange: (key: K) => void }) {
  const t = useT()
  return (
    <div className="mb-3 flex gap-1 border-b border-line" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          role="tab"
          aria-selected={value === tab.key}
          onClick={() => onChange(tab.key)}
          className={cn(
            '-mb-px border-b-2 px-3 py-2 text-sm transition',
            value === tab.key ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-text',
          )}
        >
          {typeof tab.label === 'string' ? t(tab.label) : tab.label}
        </button>
      ))}
    </div>
  )
}

// 弹窗叠放：只有最上层的弹窗对辅助技术可见，下层弹窗标记为 aria-hidden
let modalStack: string[] = []
const modalListeners = new Set<() => void>()
const emitModals = () => modalListeners.forEach((l) => l())
const subscribeModals = (l: () => void) => { modalListeners.add(l); return () => { modalListeners.delete(l) } }
const topModal = () => modalStack[modalStack.length - 1] ?? ''

export function Modal({ open, title, onClose, children, footer, wide }: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  wide?: boolean
}) {
  const t = useT()
  const id = useId()
  const top = useSyncExternalStore(subscribeModals, topModal)
  useEffect(() => {
    if (!open) return
    modalStack = [...modalStack, id]
    emitModals()
    return () => { modalStack = modalStack.filter((x) => x !== id); emitModals() }
  }, [open, id])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && (!top || top === id) && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose, top, id])
  if (!open) return null
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-label={t(title)}
        aria-hidden={top && top !== id ? true : undefined}
        className={cn('max-h-[90vh] w-full overflow-auto rounded-lg border border-line bg-panel shadow-xl', wide ? 'max-w-3xl' : 'max-w-lg')}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="font-semibold">{t(title)}</h2>
          <button type="button" aria-label={t('关闭')} onClick={onClose} className="text-muted hover:text-text">
            <X className="size-4" />
          </button>
        </header>
        <div className="p-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-line px-4 py-2.5">{footer}</footer>}
      </div>
    </div>,
    document.body,
  )
}

export function Spinner({ text = '加载中…' }: { text?: string }) {
  const t = useT()
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-muted">
      <Loader2 className="size-4 animate-spin" />
      {t(text)}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  const t = useT()
  return <div className="py-6 text-center text-sm text-muted">{typeof children === 'string' ? t(children) : children}</div>
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const t = useT()
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
      <span>{message}</span>
      {onRetry && (
        <Button variant="ghost" onClick={onRetry}>
          {t('重试')}
        </Button>
      )}
    </div>
  )
}
