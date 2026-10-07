import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useBlocker, UNSAFE_DataRouterContext } from 'react-router-dom'
import { useContext } from 'react'
import { allowDiscardDrafts, hasSettingsDrafts, markDraft } from '@/utils/settingsDrafts'

function fingerprint(root: HTMLDivElement) {
  return JSON.stringify(Array.from(root.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>('input:not([type=file]),select,textarea')).map((field) => [field.name, field.getAttribute('aria-label'), field.value, 'checked' in field && field.checked]))
}

export function SettingsDraftBoundary({ id, paths, children }: { id: string; paths: string[]; children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null)
  const baseline = useRef('')
  const submitted = useRef('')
  const dirtyRef = useRef(false)
  const [dirty, setDirty] = useState(false)
  const pathKey = JSON.stringify(paths)
  useEffect(() => {
    const element = root.current!
    const mark = (value: boolean) => { dirtyRef.current = value; setDirty(value); markDraft(id, value) }
    const observer = new MutationObserver(() => { if (!dirtyRef.current) baseline.current = fingerprint(element) })
    baseline.current = fingerprint(element)
    observer.observe(element, { subtree: true, childList: true, attributes: true })
    const start = (event: Event) => {
      if ((JSON.parse(pathKey) as string[]).some((path) => (event as CustomEvent<string>).detail.startsWith(path))) submitted.current = fingerprint(element)
    }
    const saved = (event: Event) => {
      if ((JSON.parse(pathKey) as string[]).some((path) => (event as CustomEvent<string>).detail.startsWith(path)) && submitted.current === fingerprint(element)) {
        baseline.current = submitted.current
        mark(false)
      }
    }
    const reset = (event: Event) => { if ((event as CustomEvent<string>).detail === id) { baseline.current = fingerprint(element); mark(false) } }
    const change = () => {
      mark(fingerprint(element) !== baseline.current)
      // React 的状态和 DOM 提交可能晚于原生 change 捕获。
      queueMicrotask(() => mark(fingerprint(element) !== baseline.current))
    }
    element.addEventListener('input', change)
    element.addEventListener('change', change)
    window.addEventListener('settings:save-start', start)
    window.addEventListener('settings:saved', saved)
    window.addEventListener('settings:baseline-reset', reset)
    return () => {
      observer.disconnect()
      element.removeEventListener('input', change)
      element.removeEventListener('change', change)
      window.removeEventListener('settings:save-start', start)
      window.removeEventListener('settings:saved', saved)
      window.removeEventListener('settings:baseline-reset', reset)
      markDraft(id, false)
    }
  }, [id, pathKey])
  return <div ref={root} data-draft-id={id}>{dirty && <p role="status" className="mb-2 text-sm text-warn">有未保存的设置</p>}{children}</div>
}

function RouterGuard() {
  const blocker = useBlocker(() => hasSettingsDrafts())
  useEffect(() => {
    if (blocker.state === 'blocked') {
      if (allowDiscardDrafts()) blocker.proceed()
      else blocker.reset()
    }
  }, [blocker])
  return null
}

export function SettingsNavigationGuard() {
  const router = useContext(UNSAFE_DataRouterContext)
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (hasSettingsDrafts()) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [])
  return router ? <RouterGuard /> : null
}
