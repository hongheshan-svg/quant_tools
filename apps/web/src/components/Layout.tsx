// 页面框架：左侧导航、顶部股票搜索、任务中心、主题切换
import {
  Activity, BarChart3, Bell, BookOpen, Crosshair, Filter, Flame, Gauge, History, LayoutDashboard, LineChart, LogOut, Menu,
  MessagesSquare, Microscope, Moon, Newspaper, Settings, Star, Sun, Wallet,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import { useLang, useT } from '@/i18n'
import { useThemeStore } from '@/stores/theme'
import { cn } from '@/utils/cn'
import { ErrorBoundary } from './ErrorBoundary'
import { StockSearch } from './StockSearch'
import { TaskCenter } from './TaskCenter'

export const NAV: { group: string; items: { to: string; label: string; icon: ReactNode }[] }[] = [
  {
    group: '交易',
    items: [
      { to: '/', label: '交易决策', icon: <LayoutDashboard className="size-4" /> },
      { to: '/news', label: '资讯流', icon: <Newspaper className="size-4" /> },
      { to: '/chat', label: 'AI 问股', icon: <MessagesSquare className="size-4" /> },
      { to: '/watchlist', label: '自选股', icon: <Star className="size-4" /> },
    ],
  },
  {
    group: '账户',
    items: [
      { to: '/trading', label: '模拟交易', icon: <Wallet className="size-4" /> },
      { to: '/real', label: '实盘记账', icon: <BookOpen className="size-4" /> },
    ],
  },
  {
    group: '分析',
    items: [
      { to: '/review', label: '大盘复盘', icon: <LineChart className="size-4" /> },
      { to: '/themes', label: '主线分析', icon: <Flame className="size-4" /> },
      { to: '/research', label: '深度研究', icon: <Microscope className="size-4" /> },
      { to: '/screening', label: '策略选股', icon: <Filter className="size-4" /> },
      { to: '/performance', label: '信号绩效', icon: <BarChart3 className="size-4" /> },
      { to: '/history', label: '诊断历史', icon: <History className="size-4" /> },
      { to: '/signals', label: '决策信号', icon: <Crosshair className="size-4" /> },
      { to: '/alerts', label: '盘中提醒', icon: <Bell className="size-4" /> },
    ],
  },
  {
    group: '系统',
    items: [
      { to: '/sources', label: '数据源状态', icon: <Activity className="size-4" /> },
      { to: '/usage', label: 'AI 用量', icon: <Gauge className="size-4" /> },
      { to: '/settings', label: '设置', icon: <Settings className="size-4" /> },
    ],
  },
]

export function Layout({ authEnabled }: { authEnabled: boolean }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const { theme, toggle } = useThemeStore()
  const navigate = useNavigate()
  const location = useLocation()
  const t = useT()
  const [lang, setLang] = useLang()

  const nav = (
    <nav className="flex flex-col gap-4 p-3">
      {NAV.map((g) => (
        <div key={g.group}>
          <div className="mb-1 px-2 text-[11px] tracking-wide text-muted">{t(g.group)}</div>
          {g.items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) =>
                cn('flex items-center gap-2 rounded-md px-2 py-1.5 text-sm', isActive ? 'bg-panel-2 text-accent' : 'text-muted hover:text-text')
              }
            >
              {item.icon}
              {t(item.label)}
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  )

  return (
    <div className="flex h-full">
      <aside className="hidden w-48 shrink-0 overflow-y-auto border-r border-line bg-panel md:block">
        <div className="px-4 pt-4 text-sm font-semibold">{t('A股量化')}</div>
        {nav}
      </aside>
      {menuOpen && (
        <div className="fixed inset-0 z-50 bg-black/50 md:hidden" onClick={() => setMenuOpen(false)}>
          <aside className="h-full w-56 overflow-y-auto bg-panel" onClick={(e) => e.stopPropagation()}>
            {nav}
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-line bg-panel px-3 py-2">
          <button type="button" className="text-muted md:hidden" aria-label={t('菜单')} onClick={() => setMenuOpen(true)}>
            <Menu className="size-5" />
          </button>
          <StockSearch className="max-w-sm flex-1" onSelect={(s) => navigate(`/stocks/${s.code}`)} />
          <div className="ml-auto flex items-center gap-2">
            <TaskCenter />
            <button type="button" aria-label={t('切换主题')} onClick={toggle} className="rounded-md border border-line p-1.5 text-muted hover:text-text">
              {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
            <button
              type="button"
              aria-label={t('切换语言')}
              onClick={() => setLang(lang === 'en' ? 'zh' : 'en')}
              className="rounded-md border border-line px-2 py-1.5 text-xs text-muted hover:text-text"
            >
              {lang === 'en' ? '中' : 'EN'}
            </button>
            {authEnabled && (
              <button
                type="button"
                aria-label={t('退出登录')}
                onClick={async () => {
                  await api.logout()
                  window.location.reload()
                }}
                className="rounded-md border border-line p-1.5 text-muted hover:text-text"
              >
                <LogOut className="size-4" />
              </button>
            )}
          </div>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto p-3 md:p-5">
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
