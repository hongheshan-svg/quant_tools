import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'
import { api } from '@/api/endpoints'
import { EN, t, translate, useLang, useT } from '@/i18n'
import { LoginPage } from '@/pages/LoginPage'
import { useLangStore } from '@/stores/lang'

function stubFetch(routes: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const key = Object.keys(routes).find((k) => url.includes(k))
    const body = key ? routes[key] : []
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

function resetLang() {
  vi.restoreAllMocks()
  useLangStore.getState().setLang('zh')
  try { localStorage.removeItem('quant-lang') } catch { /* ignore */ }
}

beforeEach(resetLang)
afterEach(() => {
  vi.unstubAllGlobals()
  resetLang()
})

// 词典里一定存在的 key 若尚未翻译，直接让用例失败并说明
function en(key: string): string {
  const v = EN[key]
  expect(v, `EN 缺少 ${key}`).toBeTruthy()
  return v
}

describe('translate / t', () => {
  it('returns the original text in zh', () => {
    expect(translate('zh', '设置')).toBe('设置')
    expect(translate('zh', '共 {n} 条', { n: 3 })).toBe('共 3 条')
  })

  it('falls back to the original text when missing in en', () => {
    expect(translate('en', '这句话不在词典里_xyz')).toBe('这句话不在词典里_xyz')
    expect(translate('en', '不在词典 {n}', { n: 2 })).toBe('不在词典 2')
  })

  it('looks up EN and fills placeholders', () => {
    const key = Object.keys(EN).find((k) => /\{\w+\}/.test(k))
    if (key) {
      const name = /\{(\w+)\}/.exec(key)![1]
      expect(translate('en', key, { [name]: 'X' })).toBe(EN[key].replaceAll(`{${name}}`, 'X'))
    }
    expect(translate('en', 'a{name}b', { name: 'Z' })).toBe('aZb')
  })

  it('keeps unknown placeholders as is', () => {
    expect(translate('en', '{a}-{b}', { a: 1 })).toBe('1-{b}')
    expect(translate('zh', '{a}-{b}')).toBe('{a}-{b}')
  })

  it('t() reads the current language', () => {
    const key = Object.keys(EN)[0]
    expect(t(key)).toBe(key)
    useLangStore.getState().setLang('en')
    expect(t(key)).toBe(EN[key])
  })

  it('EN is a merged dictionary object', () => {
    expect(typeof EN).toBe('object')
  })
})

describe('lang store', () => {
  it('defaults to zh', () => {
    expect(useLangStore.getState().lang).toBe('zh')
  })

  it('setLang persists and updates document lang', () => {
    useLangStore.getState().setLang('en')
    expect(localStorage.getItem('quant-lang')).toBe('en')
    expect(document.documentElement.lang).toBe('en')
    useLangStore.getState().setLang('zh')
    expect(localStorage.getItem('quant-lang')).toBe('zh')
    expect(document.documentElement.lang).toBe('zh-CN')
  })

  it('initial state is en when localStorage says en', async () => {
    localStorage.setItem('quant-lang', 'en')
    vi.resetModules()
    const mod = await import('@/stores/lang')
    expect(mod.useLangStore.getState().lang).toBe('en')
    localStorage.setItem('quant-lang', 'fr')
    vi.resetModules()
    const mod2 = await import('@/stores/lang')
    expect(mod2.useLangStore.getState().lang).toBe('zh')
  })

  it('still switches when localStorage throws', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    expect(() => useLangStore.getState().setLang('en')).not.toThrow()
    expect(useLangStore.getState().lang).toBe('en')
    expect(document.documentElement.lang).toBe('en')
  })

  it('falls back to zh at load time when getItem throws', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.resetModules()
    const mod = await import('@/stores/lang')
    expect(mod.useLangStore.getState().lang).toBe('zh')
  })
})

describe('hooks', () => {
  it('useT re-renders on language change', () => {
    const { result } = renderHook(() => useT())
    const key = Object.keys(EN)[0]
    expect(result.current(key)).toBe(key)
    act(() => useLangStore.getState().setLang('en'))
    expect(result.current(key)).toBe(EN[key])
    expect(result.current('a{x}', { x: 1 })).toBe('a1')
  })

  it('useLang returns lang and setter', () => {
    const { result } = renderHook(() => useLang())
    expect(result.current[0]).toBe('zh')
    act(() => result.current[1]('en'))
    expect(result.current[0]).toBe('en')
  })
})

describe('Layout language switch', () => {
  function renderApp() {
    stubFetch({})
    render(<MemoryRouter initialEntries={['/nope']}><AppRoutes authEnabled={false} /></MemoryRouter>)
  }

  it('toggles navigation between zh and en', async () => {
    renderApp()
    expect(screen.getByRole('link', { name: /自选股/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '切换语言' }))
    await waitFor(() => expect(document.documentElement.lang).toBe('en'))
    expect(screen.getByRole('link', { name: new RegExp(en('自选股')) })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: new RegExp(en('设置')) })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /自选股/ })).not.toBeInTheDocument()
    expect(localStorage.getItem('quant-lang')).toBe('en')
    fireEvent.click(screen.getByRole('button', { name: en('切换语言') }))
    expect(screen.getByRole('link', { name: /自选股/ })).toBeInTheDocument()
    expect(document.documentElement.lang).toBe('zh-CN')
  })

  it('renders in English at startup when saved', () => {
    useLangStore.getState().setLang('en')
    renderApp()
    expect(screen.getByRole('link', { name: new RegExp(en('自选股')) })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /自选股/ })).not.toBeInTheDocument()
  })
})

describe('LoginPage language', () => {
  it('has a switch button and renders English', async () => {
    vi.spyOn(api, 'login').mockResolvedValue({ ok: true })
    render(<LoginPage passwordSet onLoggedIn={() => {}} />)
    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '切换语言' }))
    expect(await screen.findByRole('button', { name: en('登录') })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '登录' })).not.toBeInTheDocument()
    expect(screen.getByLabelText(en('密码'))).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: en('切换语言') }))
    expect(await screen.findByRole('button', { name: '登录' })).toBeInTheDocument()
  })
})
