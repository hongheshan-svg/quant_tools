// 股票搜索：代码 / 名称 / 拼音首字母，上下键选择、回车打开
import { Search } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '@/api/endpoints'
import type { StockRef } from '@/api/types'
import { cn } from '@/utils/cn'

export const SEARCH_DEBOUNCE_MS = 150

export function StockSearch({ onSelect, placeholder = '搜索股票：代码 / 名称 / 拼音首字母', className, autoFocus }: {
  onSelect: (stock: StockRef) => void
  placeholder?: string
  className?: string
  autoFocus?: boolean
}) {
  const [text, setText] = useState('')
  const [results, setResults] = useState<StockRef[]>([])
  const [active, setActive] = useState(0)
  const [open, setOpen] = useState(false)
  const seq = useRef(0)

  useEffect(() => {
    const query = text.trim()
    if (!query) {
      setResults([])
      return
    }
    const id = ++seq.current
    const timer = setTimeout(async () => {
      try {
        const found = await api.searchStocks(query)
        if (id === seq.current) {
          setResults(found)
          setActive(0)
          setOpen(true)
        }
      } catch {
        // 搜索失败时不打断输入
      }
    }, SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [text])

  const choose = (stock: StockRef | undefined) => {
    if (!stock) return
    onSelect(stock)
    setText('')
    setResults([])
    setOpen(false)
  }

  return (
    <div className={cn('relative', className)}>
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted" />
      <input
        value={text}
        autoFocus={autoFocus}
        onChange={(e) => setText(e.target.value)}
        onFocus={() => results.length && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setActive((a) => Math.min(a + 1, results.length - 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setActive((a) => Math.max(a - 1, 0))
          } else if (e.key === 'Enter') {
            choose(results[active])
          } else if (e.key === 'Escape') {
            setOpen(false)
          }
        }}
        placeholder={placeholder}
        aria-label="搜索股票"
        className="w-full rounded-md border border-line bg-bg py-1.5 pr-2.5 pl-8 text-sm outline-none placeholder:text-muted focus:border-accent"
      />
      {open && results.length > 0 && (
        <ul role="listbox" className="absolute z-40 mt-1 max-h-80 w-full overflow-auto rounded-md border border-line bg-panel shadow-lg">
          {results.map((r, i) => (
            <li
              key={r.code}
              role="option"
              aria-selected={i === active}
              onMouseDown={() => choose(r)}
              onMouseEnter={() => setActive(i)}
              className={cn('flex cursor-pointer justify-between px-3 py-1.5 text-sm', i === active && 'bg-panel-2')}
            >
              <span>{r.name}</span>
              <span className="num text-muted">{r.code}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
