import { create } from 'zustand'

export type Lang = 'zh' | 'en'
const KEY = 'quant-lang'

function load(): Lang {
  try {
    return localStorage.getItem(KEY) === 'en' ? 'en' : 'zh'
  } catch {
    return 'zh'
  }
}

export function applyLang(lang: Lang) {
  document.documentElement.lang = lang === 'en' ? 'en' : 'zh-CN'
}

interface LangState {
  lang: Lang
  setLang: (lang: Lang) => void
}

export const useLangStore = create<LangState>((set) => ({
  lang: load(),
  setLang: (lang) => {
    try {
      localStorage.setItem(KEY, lang)
    } catch {
      // 隐私模式下无法保存，不影响切换
    }
    applyLang(lang)
    set({ lang })
  },
}))
