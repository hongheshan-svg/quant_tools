// 统一请求封装：带 Cookie，错误转成 ApiError；401 时广播 auth:required 让页面跳转登录
import { t } from '@/i18n'
import { allowDiscardDrafts } from '@/utils/settingsDrafts'

export const API_BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map((d) => (d as { msg?: string }).msg ?? String(d)).join('；')
  }
  return fallback
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if ((path === '/settings/import' || path === '/auth/logout' || (path.startsWith('/settings/') && init.method === 'DELETE')) && !allowDiscardDrafts()) throw new ApiError('已取消，未保存草稿仍保留', 0)
  const settingsWrite = ((path.startsWith('/settings/') && ['PUT', 'DELETE'].includes(init.method ?? '')) || path === '/auth/password') && !path.includes('/test')
  if (settingsWrite) window.dispatchEvent(new CustomEvent('settings:save-start', { detail: path }))
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const res = await fetch(`${API_BASE}${path}`, { credentials: 'include', ...init, headers })
  const isJson = res.headers.get('content-type')?.includes('application/json')
  const body = isJson ? await res.json().catch(() => null) : await res.text()
  if (!res.ok) {
    const message = detailOf(body, t('请求失败（{status}）', { status: res.status }))
    if (res.status === 401 && !path.startsWith('/auth/')) {
      window.dispatchEvent(new CustomEvent('auth:required', { detail: message }))
    }
    throw new ApiError(message, res.status)
  }
  if (settingsWrite && init.method !== 'DELETE' && !(body && typeof body === 'object' && ('error' in body || ('ok' in body && !body.ok)))) window.dispatchEvent(new CustomEvent('settings:saved', { detail: path }))
  if (path === '/settings/import') window.dispatchEvent(new Event('settings:imported'))
  return body as T
}

const json = (data: unknown) => (data === undefined ? undefined : JSON.stringify(data))

export const http = {
  get: <T>(path: string, params?: Record<string, string | number | boolean | undefined>) => {
    const query = params
      ? '?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString()
      : ''
    return request<T>(path + (query === '?' ? '' : query))
  },
  post: <T>(path: string, data?: unknown) => request<T>(path, { method: 'POST', body: json(data) }),
  put: <T>(path: string, data?: unknown) => request<T>(path, { method: 'PUT', body: json(data) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  upload: <T>(path: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<T>(path, { method: 'POST', body: form })
  },
  /** POST + SSE：按空行切分事件（一个事件可能跨多个 chunk），每个 `data:` 行的 JSON 交给 onEvent；流结束时 resolve */
  stream: async <E = unknown>(
    path: string,
    data: unknown,
    opts: { onEvent: (event: E) => void; signal?: AbortSignal },
  ): Promise<void> => {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: json(data),
      signal: opts.signal,
    })
    if (!res.ok) {
      const body = await res.json().catch(() => null)
      const message = detailOf(body, t('请求失败（{status}）', { status: res.status }))
      if (res.status === 401) window.dispatchEvent(new CustomEvent('auth:required', { detail: message }))
      throw new ApiError(message, res.status)
    }
    if (!res.body) throw new ApiError(t('浏览器不支持流式响应'), 0)
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    const flush = (block: string) => {
      const payload = block
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).replace(/^ /, ''))
        .join('\n')
      if (!payload) return
      try {
        opts.onEvent(JSON.parse(payload) as E)
      } catch {
        /* 忽略无法解析的事件 */
      }
    }
    for (;;) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      buffer = buffer.replace(/\r\n/g, '\n')
      let idx: number
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        flush(buffer.slice(0, idx))
        buffer = buffer.slice(idx + 2)
      }
      if (done) break
    }
    if (buffer.trim()) flush(buffer)
  },
}
