// 配置向导：左侧步骤（按完成状态），右侧当前步骤的配置内容
import { Check, Circle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { SetupItem } from '@/api/types'
import { Badge, Button, Card, ErrorBox, PageHeader, Spinner } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { progressText, useTask } from '@/hooks/useTask'
import { cn } from '@/utils/cn'
import { LLMSettingsForm, NotifierForm } from './SettingsPage'

function StepBody({ item, onChanged }: { item: SetupItem; onChanged: () => void }) {
  const collect = useTask()
  switch (item.key) {
    case 'llm':
      return <LLMSettingsForm />
    case 'notifier':
      return <NotifierForm />
    case 'watchlist':
      return (
        <div className="space-y-2 text-sm">
          <p className="text-muted">{item.hint}</p>
          <Link to="/watchlist" className="text-accent hover:underline">前往自选股页添加</Link>
        </div>
      )
    case 'data':
      return (
        <div className="space-y-3 text-sm">
          <p className="text-muted">首次运行需要先采集一次行情与新闻数据，耗时几分钟。</p>
          <Button variant="primary" loading={collect.running} onClick={() => collect.run(api.collect, { success: '数据采集完成' }).then(onChanged).catch(() => {})}>
            {collect.running ? `采集中 ${progressText(collect.progress)}` : '立即采集'}
          </Button>
        </div>
      )
    case 'browser':
      return (
        <div className="space-y-2 text-sm text-muted">
          <p>东方财富、同花顺和社交平台采集依赖无头 Chromium。在服务所在机器的终端运行：</p>
          <pre className="rounded bg-black/20 p-2 text-text">playwright install chromium</pre>
          <p>桌面端安装包会在后台自动安装；安装完成后点击「重新检查」。</p>
        </div>
      )
    case 'calendar':
      return <p className="text-sm text-muted">{item.hint}未获取到交易日历时，会暂按周一至周五判断交易日，不影响使用。</p>
    case 'web_auth':
      return (
        <div className="space-y-2 text-sm text-muted">
          <p>{item.hint}</p>
          <Link to="/settings?tab=security" className="text-accent hover:underline">前往登录安全设置</Link>
        </div>
      )
    default:
      return <p className="text-sm text-muted">{item.hint}</p>
  }
}

export function SetupPage() {
  const { data, error, loading, reload } = useApi(api.setupStatus)
  const [current, setCurrent] = useState('')

  useEffect(() => {
    if (data && !data.items.some((i) => i.key === current)) {
      setCurrent((data.items.find((i) => !i.done) ?? data.items[0])?.key ?? '')
    }
  }, [data, current])

  const item = data?.items.find((i) => i.key === current)
  return (
    <div>
      <PageHeader
        title="配置向导"
        description="按步骤完成首次配置；必需项完成后即可使用，可选项可稍后再配"
        actions={<Button onClick={() => void reload()}>重新检查</Button>}
      />
      {loading && !data && <Spinner text="检查中…" />}
      {error && <ErrorBox message={error} />}
      {data && (
        <>
          {data.required_missing === 0 && (
            <div className="mb-3 rounded-md border border-line bg-panel px-3 py-2 text-sm">
              必需配置已全部完成。<Link to="/" className="ml-2 text-accent hover:underline">完成，前往首页</Link>
            </div>
          )}
          <div className="grid gap-3 md:grid-cols-[16rem_1fr]">
            <Card bodyClassName="p-2">
              <ul>
                {data.items.map((i) => (
                  <li key={i.key}>
                    <button
                      type="button"
                      onClick={() => setCurrent(i.key)}
                      className={cn('flex w-full items-center gap-2 rounded px-2 py-2 text-left text-sm hover:bg-line/40', i.key === current && 'bg-line/40')}
                    >
                      {i.done ? <Check className="size-4 text-down" aria-label="已完成" /> : <Circle className="size-4 text-muted" aria-label="未完成" />}
                      <span className="flex-1">{i.label}</span>
                      {i.required && !i.done && <Badge tone="warn">必需</Badge>}
                    </button>
                  </li>
                ))}
              </ul>
              <div className="px-2 pt-2 text-xs text-muted">已完成 {data.done} / {data.total}</div>
            </Card>
            <Card bodyClassName="p-4">
              {item && (
                <>
                  <h2 className="mb-3 font-semibold">{item.label}{item.done && <span className="ml-2 text-xs font-normal text-muted">已完成</span>}</h2>
                  <StepBody item={item} onChanged={() => void reload()} />
                </>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
