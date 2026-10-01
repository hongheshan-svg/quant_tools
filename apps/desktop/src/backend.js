// 后台服务（server.py 或打包后的 quant_server）的启动、健康检查和停止。
// 不依赖 Electron，便于用 node --test 测试。
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const http = require('node:http')
const net = require('node:net')
const path = require('node:path')

const BACKEND_HOST = '127.0.0.1'
const BACKEND_NAME = 'quant_server'
const HEALTH_PATH = '/api/v1/health'

// 从 startPort 起找一个本机可用端口
function findAvailablePort(startPort = 8000, endPort = 8100, host = BACKEND_HOST) {
  return new Promise((resolve, reject) => {
    const tryPort = (port) => {
      if (port > endPort) {
        reject(new Error(`${startPort}-${endPort} 之间没有可用端口`))
        return
      }
      const server = net.createServer()
      server.once('error', () => tryPort(port + 1))
      server.once('listening', () => server.close(() => resolve(port)))
      server.listen(port, host)
    }
    tryPort(startPort)
  })
}

function backendUrl(port, pathname = '/') {
  return `http://${BACKEND_HOST}:${port}${pathname}`
}

// 开发时用仓库里的虚拟环境，没有时用系统 Python；QUANT_PYTHON 可以指定
function resolvePython(repoRoot, { env = process.env, platform = process.platform, exists = fs.existsSync } = {}) {
  if (env.QUANT_PYTHON) return env.QUANT_PYTHON
  const venv = platform === 'win32'
    ? path.join(repoRoot, '.venv', 'Scripts', 'python.exe')
    : path.join(repoRoot, '.venv', 'bin', 'python')
  if (exists(venv)) return venv
  return platform === 'win32' ? 'python' : 'python3'
}

// 打包后运行 resources/backend/quant_server/quant_server(.exe)，数据放在 workdir；
// 开发时在仓库根目录运行 server.py，沿用仓库里的 config/、data/。
function resolveBackendLaunch({ packaged, resourcesPath, repoRoot, workdir, port, env = process.env, platform = process.platform, exists = fs.existsSync }) {
  const args = ['--host', BACKEND_HOST, '--port', String(port)]
  if (env.QUANT_BACKEND_PATH || packaged) {
    const exe = env.QUANT_BACKEND_PATH
      || path.join(resourcesPath, 'backend', BACKEND_NAME, platform === 'win32' ? `${BACKEND_NAME}.exe` : BACKEND_NAME)
    return { command: exe, args: [...args, '--workdir', workdir], cwd: path.dirname(exe), mode: 'packaged' }
  }
  return {
    command: resolvePython(repoRoot, { env, platform, exists }),
    args: ['-X', 'utf8', path.join(repoRoot, 'server.py'), ...args],
    cwd: repoRoot,
    mode: 'development',
  }
}

function startBackend(launch, { onOutput = () => {}, env = process.env } = {}) {
  const child = spawn(launch.command, launch.args, {
    cwd: launch.cwd,
    env: { ...env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  })
  const decoder = new TextDecoder('utf-8')
  child.stdout.on('data', (chunk) => onOutput(decoder.decode(chunk, { stream: true })))
  child.stderr.on('data', (chunk) => onOutput(decoder.decode(chunk, { stream: true })))
  return child
}

// 轮询健康检查直到返回 200；shouldAbort() 返回原因时立即失败（例如后台进程已经退出）
function waitForHealth(url, { timeoutMs = 90000, intervalMs = 300, requestTimeoutMs = 2000, shouldAbort = () => null } = {}) {
  const started = Date.now()
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const reason = shouldAbort()
      if (reason) {
        reject(new Error(reason))
        return
      }
      if (Date.now() - started > timeoutMs) {
        reject(new Error(`后台服务 ${Math.round(timeoutMs / 1000)} 秒内没有就绪`))
        return
      }
      const req = http.get(url, { timeout: requestTimeoutMs }, (res) => {
        res.resume()
        if (res.statusCode === 200) resolve(Date.now() - started)
        else setTimeout(attempt, intervalMs)
      })
      req.on('timeout', () => req.destroy())
      req.on('error', () => setTimeout(attempt, intervalMs))
    }
    attempt()
  })
}

function exitReason(child) {
  if (!child) return '后台服务没有启动'
  if (child.exitCode !== null) return `后台服务已退出（代码 ${child.exitCode}）`
  if (child.signalCode) return `后台服务被信号 ${child.signalCode} 终止`
  return null
}

function waitForExit(child, timeoutMs) {
  if (exitReason(child)) return Promise.resolve(true)
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs)
    child.once('exit', () => {
      clearTimeout(timer)
      resolve(true)
    })
  })
}

// Windows 用 taskkill /T 连同子进程（Playwright 浏览器）一起结束；其他平台先 SIGTERM，超时再 SIGKILL
async function stopBackend(child, { platform = process.platform, graceMs = 5000, spawnFn = spawn } = {}) {
  if (!child || exitReason(child)) return
  if (platform === 'win32') {
    spawnFn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }).on('error', () => {})
    await waitForExit(child, graceMs)
    return
  }
  child.kill('SIGTERM')
  if (!(await waitForExit(child, graceMs))) {
    child.kill('SIGKILL')
    await waitForExit(child, 2000)
  }
}

// 只有 http(s) 外链交给系统浏览器打开，避免 file:、javascript: 等协议
function isExternalUrl(url, backendOrigin) {
  try {
    const u = new URL(url)
    return (u.protocol === 'http:' || u.protocol === 'https:') && u.origin !== backendOrigin
  } catch {
    return false
  }
}

// Linux 的 AppImage 挂载为 nosuid，用不了 setuid 的 chrome-sandbox；Ubuntu 23.10+ 又用 AppArmor 限制了非特权 user namespace，
// Chromium 沙箱两种方式都不可用，Electron 直接启动失败。AppImage 运行时关闭沙箱（界面只加载本机后台服务，站外链接交给系统浏览器）
function needsNoSandbox(platform = process.platform, env = process.env) {
  return platform === 'linux' && Boolean(env.APPIMAGE)
}

module.exports = {
  BACKEND_HOST,
  HEALTH_PATH,
  backendUrl,
  exitReason,
  findAvailablePort,
  isExternalUrl,
  needsNoSandbox,
  resolveBackendLaunch,
  resolvePython,
  startBackend,
  stopBackend,
  waitForHealth,
}
