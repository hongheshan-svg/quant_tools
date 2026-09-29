import { useEffect, useState } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { api } from './api/endpoints'
import type { AuthStatus } from './api/types'
import { Layout } from './components/Layout'
import { Toaster } from './components/Toaster'
import { Spinner } from './components/ui'
import { AlertsPage } from './pages/AlertsPage'
import { ChatPage } from './pages/ChatPage'
import { DashboardPage } from './pages/DashboardPage'
import { HistoryPage } from './pages/HistoryPage'
import { LoginPage } from './pages/LoginPage'
import { NewsPage } from './pages/NewsPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { PerformancePage } from './pages/PerformancePage'
import { ResearchPage } from './pages/ResearchPage'
import { RealPage } from './pages/RealPage'
import { ReviewPage } from './pages/ReviewPage'
import { ScreeningPage } from './pages/ScreeningPage'
import { SetupPage } from './pages/SetupPage'
import { SettingsPage } from './pages/SettingsPage'
import { SignalsPage } from './pages/SignalsPage'
import { SourcesPage } from './pages/SourcesPage'
import { StockPage } from './pages/StockPage'
import { ThemesPage } from './pages/ThemesPage'
import { TradingPage } from './pages/TradingPage'
import { UsagePage } from './pages/UsagePage'
import { WatchlistPage } from './pages/WatchlistPage'

const FALLBACK_AUTH: AuthStatus = { auth_enabled: false, password_set: false, logged_in: false }

export function AppRoutes({ authEnabled }: { authEnabled: boolean }) {
  return (
    <Routes>
      <Route element={<Layout authEnabled={authEnabled} />}>
        <Route index element={<DashboardPage />} />
        <Route path="news" element={<NewsPage />} />
        <Route path="chat" element={<ChatPage />} />
        <Route path="chat/:sessionId" element={<ChatPage />} />
        <Route path="watchlist" element={<WatchlistPage />} />
        <Route path="trading" element={<TradingPage />} />
        <Route path="real" element={<RealPage />} />
        <Route path="review" element={<ReviewPage />} />
        <Route path="themes" element={<ThemesPage />} />
        <Route path="research" element={<ResearchPage />} />
        <Route path="screening" element={<ScreeningPage />} />
        <Route path="performance" element={<PerformancePage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="signals" element={<SignalsPage />} />
        <Route path="alerts" element={<AlertsPage />} />
        <Route path="sources" element={<SourcesPage />} />
        <Route path="usage" element={<UsagePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="setup" element={<SetupPage />} />
        <Route path="stocks/:code" element={<StockPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

export default function App() {
  const [auth, setAuth] = useState<AuthStatus | null>(null)
  const [needLogin, setNeedLogin] = useState(false)

  useEffect(() => {
    api
      .authStatus()
      .then((s) => {
        setAuth(s)
        setNeedLogin(s.auth_enabled && !s.logged_in)
      })
      .catch(() => setAuth(FALLBACK_AUTH))
    const onRequired = () => setNeedLogin(true)
    window.addEventListener('auth:required', onRequired)
    return () => window.removeEventListener('auth:required', onRequired)
  }, [])

  if (!auth) return <Spinner text="连接服务…" />
  return (
    <>
      {needLogin ? (
        <LoginPage passwordSet={auth.password_set} onLoggedIn={() => setNeedLogin(false)} />
      ) : (
        <BrowserRouter>
          <AppRoutes authEnabled={auth.auth_enabled} />
        </BrowserRouter>
      )}
      <Toaster />
    </>
  )
}
