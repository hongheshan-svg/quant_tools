import { create } from 'zustand'
import type { Task } from '@/api/types'

interface TaskState {
  tasks: Record<string, Task>
  upsert: (task: Task) => void
  remove: (id: string) => void
}

/** 服务器恢复与界面发起的任务共用，旧轮询快照不能覆盖较新的流式进度。 */
export const useTaskStore = create<TaskState>((set) => ({
  tasks: {},
  upsert: (task) => set((s) => {
    const current = s.tasks[task.id]
    if (current && (current.revision ?? 0) > (task.revision ?? 0)) return s
    return { tasks: { ...s.tasks, [task.id]: task } }
  }),
  remove: (id) =>
    set((s) => {
      const next = { ...s.tasks }
      delete next[id]
      return { tasks: next }
    }),
}))
