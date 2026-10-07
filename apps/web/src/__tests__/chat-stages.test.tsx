import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ChatStages } from '@/components/ChatStages'

describe('阶段回放', () => {
  it('合并开始与结束，保留未知阶段与失败原因', () => {
    render(<ChatStages events={[
      { type: 'stage', stage_id: 'a', name: '未来阶段', status: 'started', elapsed_ms: 0 },
      { type: 'stage', stage_id: 'a', name: '未来阶段', status: 'timeout', elapsed_ms: 2345, reason: '等待超时' },
      { type: 'stage', stage_id: 'b', name: '下一步', status: 'budget_skipped', elapsed_ms: 0 },
    ]} />)
    expect(screen.getByText(/未来阶段.*超时.*2.3s/)).toBeInTheDocument()
    expect(screen.queryByText(/运行中/)).not.toBeInTheDocument()
    expect(screen.getByText(/预算不足，已跳过/)).toBeInTheDocument()
  })
})
