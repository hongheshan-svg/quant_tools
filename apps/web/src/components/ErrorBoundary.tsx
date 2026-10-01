import { Component, type ErrorInfo, type ReactNode } from 'react'
import { useT } from '@/i18n'
import { Button, Card } from './ui'

// 页面渲染出错时只替换内容区，侧栏和顶栏照常可用；Layout 按路径设置 key，切换页面即重置
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('页面渲染出错', error, info.componentStack)
  }

  render() {
    if (this.state.error) return <PageCrash error={this.state.error} onRetry={() => this.setState({ error: null })} />
    return this.props.children
  }
}

function PageCrash({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const t = useT()
  return (
    <Card title={t('页面出错')}>
      <p className="text-sm text-muted">{t('这个页面渲染时出错，其他页面不受影响，可以从左侧菜单继续使用。')}</p>
      <pre className="mt-2 overflow-x-auto rounded-md bg-panel-2 p-2 text-xs text-danger">{error.message}</pre>
      <div className="mt-3 flex gap-2">
        <Button onClick={onRetry}>{t('重试')}</Button>
        <Button variant="ghost" onClick={() => window.location.reload()}>{t('刷新页面')}</Button>
      </div>
    </Card>
  )
}
