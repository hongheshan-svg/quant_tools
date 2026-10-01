const assert = require('node:assert/strict')
const fs = require('node:fs')
const Module = require('node:module')
const os = require('node:os')
const path = require('node:path')
const { test } = require('node:test')

const updater = require('../src/updater')

test('compareVersions 按数字段比较', () => {
  assert.equal(updater.compareVersions('1.2.0', '1.10.0'), -1)
  assert.equal(updater.compareVersions('1.10.0', '1.2.0'), 1)
  assert.equal(updater.compareVersions('1.2.3', '1.2.3'), 0)
})

test('compareVersions 支持 v 前缀与缺段', () => {
  assert.equal(updater.compareVersions('v1.2.0', '1.2.0'), 0)
  assert.equal(updater.compareVersions('1.2', '1.2.0'), 0)
  assert.equal(updater.compareVersions('v2.0.0', '1.9.9'), 1)
})

test('compareVersions 预发布版本低于正式版', () => {
  assert.equal(updater.compareVersions('1.2.0-beta.1', '1.2.0'), -1)
  assert.equal(updater.compareVersions('1.2.0', '1.2.0-beta.1'), 1)
})

test('shouldAutoCheck', () => {
  const now = Date.now()
  const H = 3600 * 1000
  assert.equal(updater.shouldAutoCheck({ autoCheckUpdates: false }, now), false)
  assert.equal(updater.shouldAutoCheck({ autoCheckUpdates: false, lastCheck: 0 }, now), false)
  assert.equal(updater.shouldAutoCheck({ autoCheckUpdates: true }, now), true)
  assert.equal(updater.shouldAutoCheck({}, now), true)
  assert.equal(updater.shouldAutoCheck({ lastCheck: now - 1 * H }, now), false)
  assert.equal(updater.shouldAutoCheck({ lastCheck: now - 7 * H }, now), true)
})

test('canAutoInstall', () => {
  assert.equal(updater.canAutoInstall('win32', false), false)
  assert.equal(updater.canAutoInstall('linux', false), false)
  assert.equal(updater.canAutoInstall('darwin', true), false)
  assert.equal(updater.canAutoInstall('win32', true), true)
  assert.equal(updater.canAutoInstall('linux', true), true)
})

test('releasePageUrl', () => {
  const base = 'https://github.com/hongheshan-svg/quant_tools/releases'
  assert.equal(updater.releasePageUrl('1.2.3'), `${base}/tag/v1.2.3`)
  assert.equal(updater.releasePageUrl(''), `${base}/latest`)
})

test('readPrefs / writePrefs', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'updater-'))
  try {
    const file = path.join(dir, 'sub', 'prefs.json')
    assert.deepEqual(updater.readPrefs(file), {})
    updater.writePrefs(file, { autoCheckUpdates: false, lastCheck: 5 })
    assert.deepEqual(updater.readPrefs(file), { autoCheckUpdates: false, lastCheck: 5 })
    fs.writeFileSync(file, '{坏的')
    assert.deepEqual(updater.readPrefs(file), {})
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('preload 暴露更新相关 IPC', async () => {
  const exposed = {}
  const invoked = []
  const fake = {
    contextBridge: { exposeInMainWorld: (k, api) => { exposed[k] = api } },
    ipcRenderer: { invoke: (...a) => { invoked.push(a); return Promise.resolve(null) } },
  }
  const orig = Module._load
  Module._load = function load(request, ...rest) {
    return request === 'electron' ? fake : orig.call(this, request, ...rest)
  }
  delete require.cache[require.resolve('../preload')]
  try { require('../preload') } finally { Module._load = orig }
  const api = exposed.quantDesktop
  await api.checkForUpdates()
  await api.getPrefs()
  await api.setPrefs({ autoCheckUpdates: false })
  assert.deepEqual(invoked, [
    ['desktop:check-update'],
    ['desktop:get-prefs'],
    ['desktop:set-prefs', { autoCheckUpdates: false }],
  ])
})

test('package.json 声明 electron-updater 与 GitHub 发布源', () => {
  const pkg = require('../package.json')
  assert.ok(pkg.dependencies && pkg.dependencies['electron-updater'])
  const pub = [].concat(pkg.build.publish)
  assert.ok(pub.some((p) => p.provider === 'github' && p.owner === 'hongheshan-svg' && p.repo === 'quant_tools'))
})

test('发布工作流上传三个平台的安装包、latest*.yml 与 blockmap', () => {
  const file = path.join(__dirname, '..', '..', '..', '.github', 'workflows', 'release.yml')
  const text = fs.readFileSync(file, 'utf8')
  assert.match(text, /latest\*\.yml/)
  assert.match(text, /\*\.blockmap/)
  for (const ext of ['exe', 'dmg', 'AppImage']) assert.match(text, new RegExp(`\\*\\.${ext}`))
})
