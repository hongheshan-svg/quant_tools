import { create } from 'zustand'
import type { Task } from '@/api/types'

interface TaskState {
  tasks: Record<string, Task>
  upsert: (task: Task) => void
  remove: (id: string) => void
}

/** 界面发起的后台任务（任务中心显示进度） */
export const useTaskStore = create<TaskState>((set) => ({
  tasks: {},
  upsert: (task) => set((s) => ({ tasks: { ...s.tasks, [task.id]: task } })),
  remove: (id) =>
    set((s) => {
      const next = { ...s.tasks }
      delete next[id]
      return { tasks: next }
    }),
}))
