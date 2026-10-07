// 草稿只留在页面内存；保存成功清除对应表单，失败和读回失败均保留。
const drafts = new Set<string>()
export function markDraft(key: string, dirty: boolean) {
  if (dirty) drafts.add(key)
  else drafts.delete(key)
  window.dispatchEvent(new Event('settings:drafts'))
}
export function hasSettingsDrafts() { return drafts.size > 0 }
export function allowDiscardDrafts() {
  return !hasSettingsDrafts() || window.confirm('有未保存的设置，离开或覆盖会丢弃草稿。确定继续吗？')
}
