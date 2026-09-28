// Electron 桌面端：启动后台服务（server.py / 打包的 quant_server），就绪后加载它托管的 Web 界面。
// 数据目录：打包后为系统的应用数据目录（可用 QUANT_HOME 指定），开发时为仓库根目录。
const { app, BrowserWindow, Menu, dialog, ipcMain, nativeTheme, shell } = require('electron')
const fs = require('node:fs')
const path = require('node:path')
const {
  HEALTH_PATH,
  backendUrl,
  exitReason,
  findAvailablePort,
  isExternalUrl,
  resolveBackendLaunch,
  startBackend,
  stopBackend,
  waitForHealth,
} = require('./src/backend')

const REPO_ROOT = path.resolve(__dirname, '..', '..')
const LOADING_PAGE = path.join(__dirname, 'renderer', 'loading.html')

let mainWindow = null
let backend = null
let backendPort = 0
let backendOrigin = ''
let quitting = false

function dataDir() {
  if (process.env.QUANT_HOME) return process.env.QUANT_HOME
  return app.isPackaged ? app.getPath('userData') : REPO_ROOT
}

function logDir() {
  return path.join(dataDir(), 'logs')
}

let logStream = null
function log(message) {
  if (!logStream) {
    fs.mkdirSync(logDir(), { recursive: true })
    logStream = fs.createWriteStream(path.join(logDir(), 'desktop.log'), { flags: 'a' })
  }
  const text = String(message).replace(/\s+$/, '')
  if (text) logStream.write(`${new Date().toISOString()} ${text}\n`)
}

function showError(error) {
  if (!mainWindow || mainWindow.isDestroyed()) return
  const query = { error: String(error?.message ?? error), log: path.join(logDir(), 'desktop.log') }
  void mainWindow.loadFile(LOADING_PAGE, { query })
}

async function launchBackend() {
  const port = await findAvailablePort(8000, 8100)
  const launch = resolveBackendLaunch({
    packaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    repoRoot: REPO_ROOT,
    workdir: dataDir(),
    port,
  })
  if (launch.mode === 'packaged' && !fs.existsSync(launch.command)) {
    throw new Error(`找不到后台程序：${launch.command}`)
  }
  log(`[backend] ${launch.mode}: ${launch.command} ${launch.args.join(' ')} (cwd=${launch.cwd})`)
  let spawnError = null
  backend = startBackend(launch, { onOutput: (text) => log(`[backend] ${text}`) })
  backend.on('error', (error) => {
    spawnError = error
    log(`[backend] 启动失败：${error.message}`)
  })
  const child = backend
  child.on('exit', (code, signal) => {
    log(`[backend] 退出 code=${code} signal=${signal ?? ''}`)
    // 启动阶段的退出由 waitForHealth 报告，这里只处理运行中途的崩溃
    if (child.ready && !quitting && !child.stopping) showError(`后台服务意外退出（代码 ${code ?? signal}），详情见日志`)
  })
  const elapsed = await waitForHealth(backendUrl(port, HEALTH_PATH), {
    shouldAbort: () => (spawnError ? `后台服务启动失败：${spawnError.message}` : exitReason(child)),
  })
  log(`[backend] 就绪，用时 ${elapsed}ms`)
  child.ready = true
  backendPort = port
  backendOrigin = new URL(backendUrl(port)).origin
  return backendUrl(port)
}

function buildMenu() {
  const template = [
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    {
      label: '文件',
      submenu: [
        { label: '打开数据目录', click: () => shell.openPath(dataDir()) },
        { label: '打开日志目录', click: () => shell.openPath(logDir()) },
        { type: 'separator' },
        process.platform === 'darwin' ? { role: 'close', label: '关闭窗口' } : { role: 'quit', label: '退出' },
      ],
    },
    { role: 'editMenu', label: '编辑' },
    {
      label: '视图',
      submenu: [
        { role: 'reload', label: '重新加载' },
        { role: 'toggleDevTools', label: '开发者工具' },
        { type: 'separator' },
        { role: 'resetZoom', label: '实际大小' },
        { role: 'zoomIn', label: '放大' },
        { role: 'zoomOut', label: '缩小' },
        { type: 'separator' },
        { role: 'togglefullscreen', label: '全屏' },
      ],
    },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 680,
    title: 'A股量化',
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#0b1020' : '#f5f7fb',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: [`--quant-desktop-version=${app.getVersion()}`],
    },
  })
  mainWindow.on('closed', () => {
    mainWindow = null
  })
  // 新窗口和站外链接交给系统浏览器
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isExternalUrl(url, backendOrigin)) void shell.openExternal(url)
    return { action: 'deny' }
  })
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (url.startsWith('file:')) return
    if (isExternalUrl(url, backendOrigin)) {
      event.preventDefault()
      void shell.openExternal(url)
    }
  })
  await loadApp()
}

// 显示启动页 → 启动（或复用）后台服务 → 加载 Web 界面
async function loadApp() {
  await mainWindow.loadFile(LOADING_PAGE)
  try {
    const url = backend && !exitReason(backend) ? backendUrl(backendPort) : await launchBackend()
    await mainWindow.loadURL(url)
  } catch (error) {
    log(`[startup] ${error.stack ?? error}`)
    showError(error)
  }
}

async function stopCurrentBackend() {
  if (!backend) return
  const child = backend
  child.stopping = true
  await stopBackend(child)
  if (backend === child) backend = null
}

ipcMain.handle('desktop:retry', async () => {
  await stopCurrentBackend()
  if (mainWindow) await loadApp()
})
ipcMain.handle('desktop:open-path', (_event, which) => shell.openPath(which === 'logs' ? logDir() : dataDir()))
ipcMain.handle('desktop:info', () => ({ version: app.getVersion(), dataDir: dataDir(), packaged: app.isPackaged }))

// 同一时间只运行一个实例：两个后台服务会重复执行定时任务、争用同一个数据库
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (!mainWindow) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.focus()
  })

  app.whenReady().then(() => {
    buildMenu()
    void createWindow()
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) void createWindow()
    })
  })

  // 关闭窗口即退出（包括 macOS），后台服务随之停止
  app.on('window-all-closed', () => app.quit())

  app.on('before-quit', (event) => {
    if (quitting || !backend || exitReason(backend)) return
    event.preventDefault()
    quitting = true
    log('[backend] 正在停止')
    stopCurrentBackend()
      .catch((error) => log(`[backend] 停止失败：${error.message}`))
      .finally(() => app.quit())
  })

  process.on('uncaughtException', (error) => {
    log(`[main] ${error.stack ?? error}`)
    dialog.showErrorBox('A股量化', String(error.message ?? error))
  })
}
