import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DataTable } from '@/components/DataTable'
import { SEARCH_DEBOUNCE_MS, StockSearch } from '@/components/StockSearch'
import { api } from '@/api/endpoints'

describe('DataTable', () => {
  it('renders rows and handles clicks', () => {
    const onClick = vi.fn()
    render(<DataTable columns={[{ key: 'name', title: '名称' }, { key: 'x', title: '值', render: (r: { name: string; x: number }) => r.x * 2 }]}
      rows={[{ name: '茅台', x: 2 }]} rowKey={(r) => r.name} onRowClick={onClick} />)
    expect(screen.getByText('名称')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
    fireEvent.click(screen.getByText('茅台'))
    expect(onClick).toHaveBeenCalledWith({ name: '茅台', x: 2 })
  })

  it('shows empty text', () => {
    render(<DataTable columns={[]} rows={[]} rowKey={() => 1} empty="没有数据" />)
    expect(screen.getByText('没有数据')).toBeInTheDocument()
  })
})

describe('StockSearch', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('searches with debounce and selects with keyboard', async () => {
    const search = vi.spyOn(api, 'searchStocks').mockResolvedValue([
      { code: '002401', name: '中远海科' },
      { code: '601919', name: '中远海控' },
    ])
    const onSelect = vi.fn()
    render(<StockSearch onSelect={onSelect} />)
    const input = screen.getByLabelText('搜索股票')
    fireEvent.change(input, { target: { value: 'zyhk' } })
    expect(search).not.toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS + 10)
    })
    expect(search).toHaveBeenCalledWith('zyhk')
    expect(screen.getAllByRole('option')).toHaveLength(2)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSelect).toHaveBeenCalledWith({ code: '601919', name: '中远海控' })
    expect((input as HTMLInputElement).value).toBe('')
  })
})
