import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { RunFlowView, TaskFlowDetails } from '@/components/RunFlowView'
import { SourceRunHistory } from '@/components/SourceRunHistory'
import type { RunFlow } from '@/api/types'

const flow: RunFlow = { version: 1, trace_id: 'restored-task', status: 'failed', truncated: true,
  copy_text: '运行 restored-task · failed\n失败阶段 · token=[REDACTED]', sources: [], edges: [],
  nodes: [{ id: 'restored-task:1', lane: 'data', name: '失败阶段', status: 'failed', ms: 10,
    started_at: null, ended_at: null, detail: '数据源无响应' }] }

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('expands a restored failed task and copies the exact diagnostic summary', async () => {
  const copy = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal('navigator', { clipboard: { writeText: copy } })
  const fetcher = vi.fn(async () => new Response(JSON.stringify(flow), { headers: { 'content-type': 'application/json' } }))
  vi.stubGlobal('fetch', fetcher)
  render(<TaskFlowDetails id="persisted" revision={3} />)
  expect(fetcher).not.toHaveBeenCalled()
  const details = screen.getByText('运行详情').parentElement as HTMLDetailsElement
  details.open = true
  fireEvent(details, new Event('toggle'))
  expect(await screen.findByText('失败阶段')).toBeInTheDocument()
  expect(screen.getByText('记录已截断')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '复制排障摘要' }))
  await waitFor(() => expect(copy).toHaveBeenCalledWith(flow.copy_text))
})

it('keeps the summary readable when clipboard permission is denied', async () => {
  vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
  render(<RunFlowView flow={flow} />)
  fireEvent.click(screen.getByRole('button', { name: '复制排障摘要' }))
  expect(await screen.findByText('复制失败，请手动选择摘要文本')).toBeInTheDocument()
  expect(document.querySelector('pre')?.textContent).toBe(flow.copy_text)
})

it('shows persisted fallback history separately from local reads and unknown older runs', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    summary: { runs: 2, recorded: 1, success: 1, failure: 1, fallback_runs: 1 }, items: [
      { id: 2, created_at: '2026-09-18T16:00', trade_date: '2026-09-18', status: 'partial', sources: {
        run_id: 'run-2', success: 1, failure: 1, dropped: 0, fallback_datasets: ['财报'],
        local_reads: [{ dataset: '日线', source: '历史库', trade_date: '2026-09-17', rows: 1 }],
        attempts: [{ dataset: '财报', source: '首选源', ok: false, error: '连接超时', ms: 10 },
          { dataset: '财报', source: '后备源', ok: true, ms: 4 }] } },
      { id: 1, created_at: '2026-09-17T16:00', trade_date: '2026-09-17', status: 'success', sources: null },
    ],
  }), { headers: { 'content-type': 'application/json' } })))
  render(<SourceRunHistory />)
  expect(await screen.findByText(/旧运行未记录来源/)).toBeInTheDocument()
  expect(screen.getByText(/本地读取 · 日线 · 历史库/)).toBeInTheDocument()
  expect(screen.getByText(/财报 · 首选源 · 失败/)).toBeInTheDocument()
  expect(screen.getByText(/财报 · 后备源 · 成功/)).toBeInTheDocument()
})

it('reports malformed source history without crashing the screening page', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { headers: { 'content-type': 'application/json' } })))
  render(<SourceRunHistory />)
  expect(await screen.findByText('来源历史格式不兼容，请刷新后重试')).toBeInTheDocument()
})
