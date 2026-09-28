// 统一请求封装：带 Cookie，错误转成 ApiError；401 时广播 auth:required 让页面跳转登录
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
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const res = await fetch(`${API_BASE}${path}`, { credentials: 'include', ...init, headers })
  const isJson = res.headers.get('content-type')?.includes('application/json')
  const body = isJson ? await res.json().catch(() => null) : await res.text()
  if (!res.ok) {
    const message = detailOf(body, `请求失败（${res.status}）`)
    if (res.status === 401 && !path.startsWith('/auth/')) {
      window.dispatchEvent(new CustomEvent('auth:required', { detail: message }))
    }
    throw new ApiError(message, res.status)
  }
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
}
