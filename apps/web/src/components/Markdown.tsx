import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '@/utils/cn'

/** 报告、诊断、问股回答的 markdown 渲染；链接在新窗口打开 */
export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={cn('markdown text-sm', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a> }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
