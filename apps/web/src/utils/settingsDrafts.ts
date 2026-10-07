// 草稿只留在页面内存；保存成功清除对应表单，失败和读回失败均保留。
import { t } from '@/i18n'
const drafts = new Set<string>()
let writePending = false
export function isSettingsWritePending() { return writePending }
export function setSettingsWritePending(value: boolean) {
  writePending = value
  window.dispatchEvent(new Event('settings:busy'))
}
export function markDraft(key: string, dirty: boolean) {
  if (dirty) drafts.add(key)
  else drafts.delete(key)
  window.dispatchEvent(new Event('settings:drafts'))
}
export function hasSettingsDrafts() { return drafts.size > 0 }
export function allowDiscardDrafts() {
  if (writePending) return false
  return !hasSettingsDrafts() || window.confirm(t('有未保存的设置，离开或覆盖会丢弃草稿。确定继续吗？'))
}
