import { create } from 'zustand'

export type ToastKind = 'info' | 'success' | 'error'

export interface Toast {
  id: number
  kind: ToastKind
  text: string
}

interface ToastState {
  toasts: Toast[]
  push: (kind: ToastKind, text: string) => void
  dismiss: (id: number) => void
}

let seq = 0

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  push: (kind, text) => {
    const id = ++seq
    set((s) => ({ toasts: [...s.toasts, { id, kind, text }] }))
    setTimeout(() => get().dismiss(id), kind === 'error' ? 8000 : 4000)
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

export const toast = {
  info: (text: string) => useToastStore.getState().push('info', text),
  success: (text: string) => useToastStore.getState().push('success', text),
  error: (text: string) => useToastStore.getState().push('error', text),
}
