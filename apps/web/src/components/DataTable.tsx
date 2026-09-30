// 通用表格：列定义 + 数据，表头吸顶，可点击行
import type { ReactNode } from 'react'
import { useT } from '@/i18n'
import { cn } from '@/utils/cn'
import { Empty } from './ui'

export interface Column<T> {
  key: string
  title: ReactNode
  render?: (row: T, index: number) => ReactNode
  align?: 'left' | 'right' | 'center'
  className?: string
  width?: string
}

export function DataTable<T>({ columns, rows, rowKey, onRowClick, empty = '暂无数据', maxHeight, className }: {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T, index: number) => string | number
  onRowClick?: (row: T) => void
  empty?: ReactNode
  maxHeight?: string
  className?: string
}) {
  const t = useT()
  if (!rows.length) return <Empty>{empty}</Empty>
  return (
    <div className={cn('overflow-auto', className)} style={maxHeight ? { maxHeight } : undefined}>
      <table className="w-full border-collapse text-sm">
        <thead className="sticky top-0 z-10 bg-panel-2">
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                style={c.width ? { width: c.width } : undefined}
                className={cn(
                  'border-b border-line px-2.5 py-2 text-xs font-medium whitespace-nowrap text-muted',
                  c.align === 'right' ? 'text-right' : c.align === 'center' ? 'text-center' : 'text-left',
                )}
              >
                {typeof c.title === 'string' ? t(c.title) : c.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={rowKey(row, i)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={cn('border-b border-line/60 hover:bg-panel-2/60', onRowClick && 'cursor-pointer')}
            >
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={cn(
                    'px-2.5 py-1.5 align-top',
                    c.align === 'right' ? 'text-right' : c.align === 'center' ? 'text-center' : 'text-left',
                    c.className,
                  )}
                >
                  {c.render ? c.render(row, i) : String((row as Record<string, unknown>)[c.key] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
