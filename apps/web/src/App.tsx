import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useState } from 'react'
import { createBrowserRouter, RouterProvider, Route, Routes } from 'react-router-dom'
import { api } from './api/endpoints'
import type { AuthStatus } from './api/types'
import { Layout } from './components/Layout'
import { Toaster } from './components/Toaster'
import { ErrorBox, Spinner } from './components/ui'
const AlertsPage = lazy(() => import('./pages/AlertsPage').then((m) => ({ default: m.AlertsPage })))
const ChatPage = lazy(() => import('./pages/ChatPage').then((m) => ({ default: m.ChatPage })))
const DashboardPage = lazy(() => import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage })))
const HistoryPage = lazy(() => import('./pages/HistoryPage').then((m) => ({ default: m.HistoryPage })))
const LoginPage = lazy(() => import('./pages/LoginPage').then((m) => ({ default: m.LoginPage })))
const NewsPage = lazy(() => import('./pages/NewsPage').then((m) => ({ default: m.NewsPage })))
const IntelligencePage = lazy(() => import('./pages/IntelligencePage').then((m) => ({ default: m.IntelligencePage })))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage').then((m) => ({ default: m.NotFoundPage })))
const PerformancePage = lazy(() => import('./pages/PerformancePage').then((m) => ({ default: m.PerformancePage })))
const ResearchPage = lazy(() => import('./pages/ResearchPage').then((m) => ({ default: m.ResearchPage })))
const RealPage = lazy(() => import('./pages/RealPage').then((m) => ({ default: m.RealPage })))
const ReviewPage = lazy(() => import('./pages/ReviewPage').then((m) => ({ default: m.ReviewPage })))
const ScreeningPage = lazy(() => import('./pages/ScreeningPage').then((m) => ({ default: m.ScreeningPage })))
const SetupPage = lazy(() => import('./pages/SetupPage').then((m) => ({ default: m.SetupPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const SignalsPage = lazy(() => import('./pages/SignalsPage').then((m) => ({ default: m.SignalsPage })))
const SourcesPage = lazy(() => import('./pages/SourcesPage').then((m) => ({ default: m.SourcesPage })))
const StockPage = lazy(() => import('./pages/StockPage').then((m) => ({ default: m.StockPage })))
const ThemesPage = lazy(() => import('./pages/ThemesPage').then((m) => ({ default: m.ThemesPage })))
const TradingPage = lazy(() => import('./pages/TradingPage').then((m) => ({ default: m.TradingPage })))
const UsagePage = lazy(() => import('./pages/UsagePage').then((m) => ({ default: m.UsagePage })))
const WatchlistPage = lazy(() => import('./pages/WatchlistPage').then((m) => ({ default: m.WatchlistPage })))
const WorkspacePage = lazy(() => import('./pages/WorkspacePage').then((m) => ({ default: m.WorkspacePage })))

const AuthRouteContext = createContext(false)
function AuthenticatedRoutes() { return <AppRoutes authEnabled={useContext(AuthRouteContext)} /> }


export function AppRoutes({ authEnabled }: { authEnabled: boolean }) {
  return (
    <Routes>
      <Route element={<Layout authEnabled={authEnabled} />}>
        <Route index element={<Suspense fallback={<Spinner text="加载页面…" />}><WorkspacePage /></Suspense>} />
        <Route path="market" element={<Suspense fallback={<Spinner text="加载页面…" />}><DashboardPage /></Suspense>} />
        <Route path="news" element={<Suspense fallback={<Spinner text="加载页面…" />}><NewsPage /></Suspense>} />
        <Route path="intelligence" element={<Suspense fallback={<Spinner text="加载页面…" />}><IntelligencePage /></Suspense>} />
        <Route path="chat" element={<Suspense fallback={<Spinner text="加载页面…" />}><ChatPage /></Suspense>} />
        <Route path="chat/:sessionId" element={<Suspense fallback={<Spinner text="加载页面…" />}><ChatPage /></Suspense>} />
        <Route path="watchlist" element={<Suspense fallback={<Spinner text="加载页面…" />}><WatchlistPage /></Suspense>} />
        <Route path="trading" element={<Suspense fallback={<Spinner text="加载页面…" />}><TradingPage /></Suspense>} />
        <Route path="real" element={<Suspense fallback={<Spinner text="加载页面…" />}><RealPage /></Suspense>} />
        <Route path="review" element={<Suspense fallback={<Spinner text="加载页面…" />}><ReviewPage /></Suspense>} />
        <Route path="themes" element={<Suspense fallback={<Spinner text="加载页面…" />}><ThemesPage /></Suspense>} />
        <Route path="research" element={<Suspense fallback={<Spinner text="加载页面…" />}><ResearchPage /></Suspense>} />
        <Route path="screening" element={<Suspense fallback={<Spinner text="加载页面…" />}><ScreeningPage /></Suspense>} />
        <Route path="performance" element={<Suspense fallback={<Spinner text="加载页面…" />}><PerformancePage /></Suspense>} />
        <Route path="history" element={<Suspense fallback={<Spinner text="加载页面…" />}><HistoryPage /></Suspense>} />
        <Route path="signals" element={<Suspense fallback={<Spinner text="加载页面…" />}><SignalsPage /></Suspense>} />
        <Route path="alerts" element={<Suspense fallback={<Spinner text="加载页面…" />}><AlertsPage /></Suspense>} />
        <Route path="sources" element={<Suspense fallback={<Spinner text="加载页面…" />}><SourcesPage /></Suspense>} />
        <Route path="usage" element={<Suspense fallback={<Spinner text="加载页面…" />}><UsagePage /></Suspense>} />
        <Route path="settings" element={<Suspense fallback={<Spinner text="加载页面…" />}><SettingsPage /></Suspense>} />
        <Route path="setup" element={<Suspense fallback={<Spinner text="加载页面…" />}><SetupPage /></Suspense>} />
        <Route path="stocks/:code" element={<Suspense fallback={<Spinner text="加载页面…" />}><StockPage /></Suspense>} />
        <Route path="*" element={<Suspense fallback={<Spinner text="加载页面…" />}><NotFoundPage /></Suspense>} />
      </Route>
    </Routes>
  )
}

export default function App() {
  const [auth, setAuth] = useState<AuthStatus | null>(null)
  const [needLogin, setNeedLogin] = useState(false)
  const [authError, setAuthError] = useState('')
  const [router] = useState(() => createBrowserRouter([{ path: '*', element: <AuthenticatedRoutes /> }]))
  const connect = useCallback(() => {
    setAuthError('')
    return api.authStatus().then((s) => {
      setAuth(s)
      setNeedLogin(s.auth_enabled && !s.logged_in)
    }).catch((e: unknown) => setAuthError(e instanceof Error ? e.message : String(e)))
  }, [])

  useEffect(() => {
    void connect()
    const onRequired = () => setNeedLogin(true)
    window.addEventListener('auth:required', onRequired)
    return () => window.removeEventListener('auth:required', onRequired)
  }, [connect])

  if (authError) return <div className="mx-auto max-w-lg p-8"><ErrorBox message={`连接服务失败：${authError}`} onRetry={() => void connect()} /></div>
  if (!auth) return <Spinner text="连接服务…" />
  return (
    <>
      {needLogin ? (
        <Suspense fallback={<Spinner />}><LoginPage passwordSet={auth.password_set} onLoggedIn={() => setNeedLogin(false)} /></Suspense>
      ) : (
        <AuthRouteContext.Provider value={auth.auth_enabled}><RouterProvider router={router} /></AuthRouteContext.Provider>
      )}
      <Toaster />
    </>
  )
}
