import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { ReviewPage } from '@/pages/ReviewPage'
import { api } from '@/api/endpoints'
import type { MarketReview, ReviewRegion } from '@/api/types'

const report = (region: ReviewRegion): MarketReview => ({ region, trade_date: '2026-10-07', created_at: '2026-10-08', headline: `${region} 报告`, markdown: `${region} 市场证据`, stance: '', position: '' })
const open = () => render(<MemoryRouter><ReviewPage /></MemoryRouter>)
afterEach(() => vi.restoreAllMocks())

describe('regional review', () => {
  it('does not display a late response from the previous region', async () => {
    let release!: (value: MarketReview) => void
    vi.spyOn(api, 'reviewSettings').mockResolvedValue({ enabled: true, regions: ['CN'] })
    vi.spyOn(api, 'review').mockImplementation((region = 'CN') => region === 'CN' ? new Promise((resolve) => { release = resolve }) : Promise.resolve(report(region)))
    open()
    fireEvent.change(screen.getByLabelText('复盘市场'), { target: { value: 'JP' } })
    expect(await screen.findByText('JP 市场证据')).toBeTruthy()
    release(report('CN'))
    await waitFor(() => expect(screen.queryByText('CN 市场证据')).toBeNull())
    expect(screen.getByRole('link', { name: '分享图' }).getAttribute('href')).toContain('region=JP')
  })

  it('submits the selected market and keeps the previous report on failure', async () => {
    vi.spyOn(api, 'reviewSettings').mockResolvedValue({ enabled: true, regions: ['CN'] })
    vi.spyOn(api, 'review').mockImplementation((region = 'CN') => Promise.resolve(report(region)))
    const generate = vi.spyOn(api, 'generateReview').mockRejectedValue(new Error('指数不可用'))
    open()
    fireEvent.change(screen.getByLabelText('复盘市场'), { target: { value: 'KR' } })
    await screen.findByText('KR 市场证据')
    fireEvent.click(screen.getByRole('button', { name: '生成复盘' }))
    expect(await screen.findByText('Error: 指数不可用')).toBeTruthy()
    expect(generate).toHaveBeenCalledWith('KR')
    expect(screen.getByText('KR 市场证据')).toBeTruthy()
  })

  it('saves configured regions and blocks an empty selection', async () => {
    const settings = { enabled: true, regions: ['CN'] as ReviewRegion[] }
    vi.spyOn(api, 'reviewSettings').mockImplementation(async () => settings)
    vi.spyOn(api, 'review').mockResolvedValue(null)
    const save = vi.spyOn(api, 'saveReviewSettings').mockImplementation(async (value) => { Object.assign(settings, value); return value })
    open()
    const cn = await screen.findByLabelText('定时复盘-A 股')
    await waitFor(() => expect((cn as HTMLInputElement).checked).toBe(true))
    fireEvent.click(cn)
    expect((screen.getByRole('button', { name: '保存复盘范围' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByLabelText('定时复盘-日股'))
    fireEvent.click(screen.getByRole('button', { name: '保存复盘范围' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith({ enabled: true, regions: ['JP'] }))
  })
})
