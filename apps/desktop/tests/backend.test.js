const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const http = require('node:http')
const net = require('node:net')
const path = require('node:path')
const { test } = require('node:test')
const backend = require('../src/backend')

function listen(server, port = 0) {
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(server.address().port)))
}

test('findAvailablePort 跳过被占用的端口', async () => {
  const blocker = net.createServer()
  const busy = await listen(blocker)
  try {
    const port = await backend.findAvailablePort(busy, busy + 20)
    assert.notEqual(port, busy)
    assert.ok(port > busy && port <= busy + 20)
  } finally {
    blocker.close()
  }
})

test('findAvailablePort 范围内都被占用时报错', async () => {
  const blocker = net.createServer()
  const busy = await listen(blocker)
  try {
    await assert.rejects(backend.findAvailablePort(busy, busy), /没有可用端口/)
  } finally {
    blocker.close()
  }
})

test('开发模式在仓库根目录运行 server.py，优先用 .venv', () => {
  const repoRoot = path.join('/repo')
  const venv = path.join(repoRoot, '.venv', 'bin', 'python')
  const launch = backend.resolveBackendLaunch({
    packaged: false, repoRoot, workdir: '/data', port: 8123, env: {}, platform: 'darwin', exists: (p) => p === venv,
  })
  assert.equal(launch.mode, 'development')
  assert.equal(launch.command, venv)
  assert.deepEqual(launch.args, ['-X', 'utf8', path.join(repoRoot, 'server.py'), '--host', '127.0.0.1', '--port', '8123'])
  assert.equal(launch.cwd, repoRoot)
  assert.ok(!launch.args.includes('--workdir'), '开发时沿用仓库的 config/ 和 data/')
})

test('开发模式没有 .venv 时用系统 Python，QUANT_PYTHON 可以覆盖', () => {
  const common = { packaged: false, repoRoot: '/repo', workdir: '/data', port: 1, exists: () => false }
  assert.equal(backend.resolveBackendLaunch({ ...common, env: {}, platform: 'win32' }).command, 'python')
  assert.equal(backend.resolveBackendLaunch({ ...common, env: {}, platform: 'linux' }).command, 'python3')
  assert.equal(backend.resolveBackendLaunch({ ...common, env: { QUANT_PYTHON: '/opt/py' }, platform: 'linux' }).command, '/opt/py')
})

test('打包后运行 resources 里的 quant_server，数据放在用户目录', () => {
  const launch = backend.resolveBackendLaunch({
    packaged: true, resourcesPath: '/app/resources', repoRoot: '/repo', workdir: '/home/u/AStockQuant', port: 8001, env: {}, platform: 'win32',
  })
  assert.equal(launch.mode, 'packaged')
  assert.equal(launch.command, path.join('/app/resources', 'backend', 'quant_server', 'quant_server.exe'))
  assert.deepEqual(launch.args.slice(-2), ['--workdir', '/home/u/AStockQuant'])
  assert.equal(launch.cwd, path.dirname(launch.command))

  const custom = backend.resolveBackendLaunch({
    packaged: false, repoRoot: '/repo', workdir: '/w', port: 8001, env: { QUANT_BACKEND_PATH: '/tmp/qs' }, platform: 'linux',
  })
  assert.equal(custom.command, '/tmp/qs')
  assert.equal(custom.mode, 'packaged')
})

test('waitForHealth 等到健康检查返回 200', async () => {
  let calls = 0
  const server = http.createServer((req, res) => {
    calls += 1
    res.statusCode = req.url === backend.HEALTH_PATH && calls >= 3 ? 200 : 503
    res.end('{}')
  })
  const port = await listen(server)
  try {
    const elapsed = await backend.waitForHealth(backend.backendUrl(port, backend.HEALTH_PATH), { intervalMs: 10, timeoutMs: 5000 })
    assert.ok(elapsed >= 0)
    assert.equal(calls, 3)
  } finally {
    server.close()
  }
})

test('waitForHealth 在后台进程退出时立即失败，超时也会失败', async () => {
  const port = await backend.findAvailablePort(18300, 18400)
  const url = backend.backendUrl(port, backend.HEALTH_PATH)
  let probes = 0
  await assert.rejects(
    backend.waitForHealth(url, { intervalMs: 10, shouldAbort: () => (++probes > 2 ? '后台服务已退出（代码 1）' : null) }),
    /已退出/,
  )
  await assert.rejects(backend.waitForHealth(url, { intervalMs: 10, timeoutMs: 50 }), /没有就绪/)
})

class FakeChild extends EventEmitter {
  constructor({ ignoreTerm = false } = {}) {
    super()
    this.pid = 4321
    this.exitCode = null
    this.signalCode = null
    this.signals = []
    this.ignoreTerm = ignoreTerm
  }

  kill(signal) {
    this.signals.push(signal)
    if (signal === 'SIGKILL' || !this.ignoreTerm) {
      setImmediate(() => {
        this.signalCode = signal
        this.emit('exit', null, signal)
      })
    }
  }
}

test('stopBackend 先 SIGTERM，不退出再 SIGKILL', async () => {
  const polite = new FakeChild()
  await backend.stopBackend(polite, { platform: 'linux', graceMs: 50 })
  assert.deepEqual(polite.signals, ['SIGTERM'])

  const stubborn = new FakeChild({ ignoreTerm: true })
  await backend.stopBackend(stubborn, { platform: 'darwin', graceMs: 30 })
  assert.deepEqual(stubborn.signals, ['SIGTERM', 'SIGKILL'])
  assert.equal(backend.exitReason(stubborn), '后台服务被信号 SIGKILL 终止')
})

test('stopBackend 在 Windows 上用 taskkill 结束整个进程树', async () => {
  const child = new FakeChild()
  const calls = []
  const spawnFn = (cmd, args) => {
    calls.push([cmd, ...args])
    setImmediate(() => {
      child.exitCode = 1
      child.emit('exit', 1, null)
    })
    return new EventEmitter()
  }
  await backend.stopBackend(child, { platform: 'win32', graceMs: 200, spawnFn })
  assert.deepEqual(calls, [['taskkill', '/PID', '4321', '/T', '/F']])
  assert.deepEqual(child.signals, [])
})

test('stopBackend 对已退出的进程不做任何事', async () => {
  const child = new FakeChild()
  child.exitCode = 0
  await backend.stopBackend(child, { platform: 'linux' })
  await backend.stopBackend(null)
  assert.deepEqual(child.signals, [])
})

test('isExternalUrl 只放行站外的 http(s) 链接', () => {
  const origin = 'http://127.0.0.1:8000'
  assert.equal(backend.isExternalUrl('https://www.cls.cn/detail/1', origin), true)
  assert.equal(backend.isExternalUrl('http://127.0.0.1:8000/stocks/600519', origin), false)
  assert.equal(backend.isExternalUrl('file:///etc/passwd', origin), false)
  assert.equal(backend.isExternalUrl('javascript:alert(1)', origin), false)
  assert.equal(backend.isExternalUrl('not a url', origin), false)
})
