import { useT } from '@/i18n'
import { useToastStore } from '@/stores/toast'
import { cn } from '@/utils/cn'

export function Toaster() {
  const { toasts, dismiss } = useToastStore()
  const t = useT()
  return (
    <div className="fixed right-4 bottom-4 z-[60] flex w-80 flex-col gap-2" aria-live="polite">
      {toasts.map((item) => (
        <div
          key={item.id}
          role="status"
          onClick={() => dismiss(item.id)}
          className={cn(
            'cursor-pointer rounded-md border px-3 py-2 text-sm shadow-lg',
            item.kind === 'error' ? 'border-danger/50 bg-panel text-danger' : item.kind === 'success' ? 'border-down/50 bg-panel text-down' : 'border-line bg-panel text-text',
          )}
        >
          {t(item.text)}
        </div>
      ))}
    </div>
  )
}
