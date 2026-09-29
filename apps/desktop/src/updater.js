// 自动更新的纯逻辑（不依赖 Electron，便于 node --test）：版本比较、检查间隔、偏好文件读写
const fs = require('node:fs')
const path = require('node:path')

const REPO_URL = 'https://github.com/hongheshan-svg/quant_tools'
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000

// 解析成 { nums, pre }：忽略前缀 v 和构建元数据（+xxx），-xxx 为预发布后缀
function parseVersion(value) {
  const text = String(value ?? '').trim().replace(/^v/i, '').split('+')[0]
  const dash = text.indexOf('-')
  const core = dash === -1 ? text : text.slice(0, dash)
  const pre = dash === -1 ? '' : text.slice(dash + 1)
  const nums = core.split('.').map((part) => {
    const n = parseInt(part, 10)
    return Number.isNaN(n) ? 0 : n
  })
  return { nums, pre }
}

function comparePre(a, b) {
  const pa = a.split('.')
  const pb = b.split('.')
  for (let i = 0; i < Math.max(pa.length, pb.length); i += 1) {
    if (pa[i] === undefined) return -1
    if (pb[i] === undefined) return 1
    const na = /^\d+$/.test(pa[i])
    const nb = /^\d+$/.test(pb[i])
    if (na && nb) {
      const diff = Number(pa[i]) - Number(pb[i])
      if (diff) return diff < 0 ? -1 : 1
    } else if (na !== nb) {
      return na ? -1 : 1
    } else if (pa[i] !== pb[i]) {
      return pa[i] < pb[i] ? -1 : 1
    }
  }
  return 0
}

function compareVersions(a, b) {
  const va = parseVersion(a)
  const vb = parseVersion(b)
  for (let i = 0; i < Math.max(va.nums.length, vb.nums.length); i += 1) {
    const x = va.nums[i] ?? 0
    const y = vb.nums[i] ?? 0
    if (x !== y) return x < y ? -1 : 1
  }
  if (va.pre === vb.pre) return 0
  if (!va.pre) return 1
  if (!vb.pre) return -1
  return comparePre(va.pre, vb.pre)
}

// 开启自动检查（默认开）且距上次检查超过 6 小时
function shouldAutoCheck(prefs, now = Date.now()) {
  if (prefs?.autoCheckUpdates === false) return false
  const last = Number(prefs?.lastCheck)
  if (!prefs?.lastCheck || !Number.isFinite(last)) return true
  return now - last > CHECK_INTERVAL_MS
}

// 未签名的 macOS 应用无法自动安装更新，只能提示去下载页
function canAutoInstall(platform, isPackaged) {
  if (!isPackaged) return false
  return platform !== 'darwin'
}

function releasePageUrl(version) {
  const v = String(version ?? '').trim().replace(/^v/i, '')
  return v ? `${REPO_URL}/releases/tag/v${v}` : `${REPO_URL}/releases/latest`
}

function readPrefs(file) {
  try {
    const data = JSON.parse(fs.readFileSync(file, 'utf8'))
    return data && typeof data === 'object' && !Array.isArray(data) ? data : {}
  } catch {
    return {}
  }
}

function writePrefs(file, prefs) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, JSON.stringify(prefs ?? {}, null, 2), 'utf8')
}

module.exports = {
  CHECK_INTERVAL_MS,
  canAutoInstall,
  compareVersions,
  readPrefs,
  releasePageUrl,
  shouldAutoCheck,
  writePrefs,
}
