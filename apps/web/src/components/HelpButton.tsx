// 带「?」图标的帮助按钮：点击弹出对应设置标签的说明
import { CircleHelp } from 'lucide-react'
import { useState } from 'react'
import { Modal } from '@/components/ui'
import { SETTINGS_HELP } from '@/utils/settingsHelp'

export function HelpButton({ helpKey }: { helpKey: string }) {
  const [open, setOpen] = useState(false)
  const help = SETTINGS_HELP[helpKey]
  if (!help) return null
  return (
    <>
      <button
        type="button"
        aria-label="帮助"
        title="查看帮助"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-muted hover:bg-line/40 hover:text-text"
      >
        <CircleHelp className="size-4" />
        帮助
      </button>
      <Modal open={open} title={help.title} onClose={() => setOpen(false)} wide>
        <p className="mb-3 text-sm text-muted">{help.summary}</p>
        <dl className="space-y-3 text-sm">
          {help.items.map((i) => (
            <div key={i.label}>
              <dt className="font-medium">{i.label}</dt>
              <dd className="mt-0.5 text-muted">{i.text}</dd>
            </div>
          ))}
        </dl>
      </Modal>
    </>
  )
}
