import { create } from 'zustand'

export type ThemeName = 'dark' | 'light'
const KEY = 'quant-theme'

function load(): ThemeName {
  try {
    return localStorage.getItem(KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function applyTheme(theme: ThemeName) {
  document.documentElement.dataset.theme = theme
}

interface ThemeState {
  theme: ThemeName
  toggle: () => void
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: load(),
  toggle: () => {
    const theme: ThemeName = get().theme === 'dark' ? 'light' : 'dark'
    try {
      localStorage.setItem(KEY, theme)
    } catch {
      // 隐私模式下无法保存，不影响切换
    }
    applyTheme(theme)
    set({ theme })
  },
}))
