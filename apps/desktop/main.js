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
  needsNoSandbox,
  resolveBackendLaunch,
  startBackend,
  stopBackend,
  waitForHealth,
} = require('./src/backend')
const { canAutoInstall, readPrefs, releasePageUrl, shouldAutoCheck, writePrefs } = require('./src/updater')

const REPO_ROOT = path.resolve(__dirname, '..', '..')
const LOADING_PAGE = path.join(__dirname, 'renderer', 'loading.html')

if (needsNoSandbox()) app.commandLine.appendSwitch('no-sandbox')

let mainWindow = null
let backend = null
let backendPort = 0
let backendOrigin = ''
let quitting = false
let updateChecked = false
let updateBusy = false // 正在下载或弹窗中，避免重复检查

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
    {
      label: '帮助',
      submenu: [{ label: '检查更新…', click: () => void manualCheckUpdate() }],
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
    if (!updateChecked) {
      updateChecked = true
      scheduleAutoCheck()
    }
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

// ---- 自动更新 ----
function prefsFile() {
  return path.join(dataDir(), 'desktop-prefs.json')
}

function getAutoUpdater() {
  const { autoUpdater } = require('electron-updater')
  if (!autoUpdater.__quantInit) {
    autoUpdater.__quantInit = true
    autoUpdater.autoDownload = false
    autoUpdater.logger = { info: (m) => log(`[updater] ${m}`), warn: (m) => log(`[updater] ${m}`), error: (m) => log(`[updater] ${m}`), debug: () => {} }
    autoUpdater.on('download-progress', (p) => {
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.setProgressBar((p.percent || 0) / 100)
    })
  }
  return autoUpdater
}

function touchLastCheck() {
  const file = prefsFile()
  writePrefs(file, { ...readPrefs(file), lastCheck: Date.now() })
}

// 检查更新：返回 { status, version?, message }；不弹窗
async function checkUpdate() {
  if (!app.isPackaged) return { status: 'unsupported', message: '开发模式不支持检查更新' }
  try {
    const result = await getAutoUpdater().checkForUpdates()
    touchLastCheck()
    const version = result?.updateInfo?.version
    if (result?.isUpdateAvailable && version) return { status: 'available', version, message: `发现新版本 ${version}` }
    return { status: 'latest', version: app.getVersion(), message: '已是最新版本' }
  } catch (error) {
    log(`[updater] 检查失败：${error.stack ?? error}`)
    return { status: 'error', message: String(error?.message ?? error) }
  }
}

async function promptAndInstall(version) {
  const win = mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined
  if (!canAutoInstall(process.platform, app.isPackaged)) {
    const { response } = await dialog.showMessageBox(win, {
      type: 'info',
      title: '发现新版本',
      message: `发现新版本 ${version}，是否前往下载？`,
      detail: '当前系统暂不支持自动安装，请下载安装包后手动安装。',
      buttons: ['前往下载', '稍后'],
      defaultId: 0,
      cancelId: 1,
    })
    if (response === 0) void shell.openExternal(releasePageUrl(version))
    return
  }
  const ask = await dialog.showMessageBox(win, {
    type: 'info',
    title: '发现新版本',
    message: `发现新版本 ${version}，是否下载？`,
    buttons: ['下载', '稍后'],
    defaultId: 0,
    cancelId: 1,
  })
  if (ask.response !== 0) return
  const updater = getAutoUpdater()
  try {
    await updater.downloadUpdate()
  } catch (error) {
    log(`[updater] 下载失败：${error.stack ?? error}`)
    dialog.showErrorBox('更新失败', String(error?.message ?? error))
    return
  } finally {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.setProgressBar(-1)
  }
  const done = await dialog.showMessageBox(win, {
    type: 'info',
    title: '更新已就绪',
    message: '新版本已下载，重启后安装',
    buttons: ['立即重启', '稍后'],
    defaultId: 0,
    cancelId: 1,
  })
  if (done.response !== 0) return
  quitting = true
  try {
    await stopCurrentBackend()
  } catch (error) {
    log(`[backend] 停止失败：${error.message}`)
  }
  updater.quitAndInstall()
}

async function manualCheckUpdate() {
  if (updateBusy) return
  updateBusy = true
  try {
    const win = mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined
    const r = await checkUpdate()
    if (r.status === 'available') await promptAndInstall(r.version)
    else if (r.status === 'latest') await dialog.showMessageBox(win, { type: 'info', title: '检查更新', message: '已是最新版本', detail: `当前版本 ${app.getVersion()}` })
    else if (r.status === 'unsupported') await dialog.showMessageBox(win, { type: 'info', title: '检查更新', message: r.message })
    else await dialog.showMessageBox(win, { type: 'error', title: '检查更新失败', message: '检查更新失败', detail: r.message })
  } finally {
    updateBusy = false
  }
}

// 后台服务就绪后延迟检查一次（打包运行、开启自动检查且距上次超过 6 小时）
function scheduleAutoCheck() {
  if (!app.isPackaged || !shouldAutoCheck(readPrefs(prefsFile()))) return
  setTimeout(async () => {
    if (updateBusy || quitting) return
    updateBusy = true
    try {
      const r = await checkUpdate()
      log(`[updater] 自动检查：${r.status} ${r.version ?? r.message}`)
      if (r.status === 'available') await promptAndInstall(r.version)
    } finally {
      updateBusy = false
    }
  }, 10000)
}

ipcMain.handle('desktop:check-update', () => checkUpdate())
ipcMain.handle('desktop:get-prefs', () => ({ autoCheckUpdates: true, ...readPrefs(prefsFile()) }))
ipcMain.handle('desktop:set-prefs', (_event, prefs) => {
  const file = prefsFile()
  const next = { ...readPrefs(file) }
  if (prefs && typeof prefs.autoCheckUpdates === 'boolean') next.autoCheckUpdates = prefs.autoCheckUpdates
  writePrefs(file, next)
  return { autoCheckUpdates: true, ...next }
})

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
