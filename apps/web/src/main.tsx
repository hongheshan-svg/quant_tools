import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'
import { applyLang, useLangStore } from './stores/lang'
import { applyTheme, useThemeStore } from './stores/theme'

applyTheme(useThemeStore.getState().theme)
applyLang(useLangStore.getState().lang)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
