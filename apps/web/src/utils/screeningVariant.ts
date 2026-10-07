// 匿名种子只留在当前浏览器，不包含账户或用户信息；禁用存储时复用内存值。
let memorySeed = ''
export function screeningVariantSeed() {
  if (memorySeed) return memorySeed
  try { memorySeed = localStorage.getItem('quant-screening-variant') || '' } catch { /* 使用内存种子 */ }
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(memorySeed)) memorySeed = crypto.randomUUID()
  try { localStorage.setItem('quant-screening-variant', memorySeed) } catch { /* 存储不可用 */ }
  return memorySeed
}
