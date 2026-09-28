// 暴露给 Web 界面的桌面端能力（window.quantDesktop），Web 端据此显示桌面端专属功能
const { contextBridge, ipcRenderer } = require('electron')

const VERSION_ARG = '--quant-desktop-version='

function readVersion(argv = process.argv) {
  const arg = argv.find((value) => typeof value === 'string' && value.startsWith(VERSION_ARG))
  return arg ? arg.slice(VERSION_ARG.length) : ''
}

function createBridge({ version = readVersion(), renderer = ipcRenderer } = {}) {
  return {
    version,
    info: () => renderer.invoke('desktop:info'),
    openDataDir: () => renderer.invoke('desktop:open-path', 'data'),
    openLogDir: () => renderer.invoke('desktop:open-path', 'logs'),
    retry: () => renderer.invoke('desktop:retry'),
  }
}

contextBridge.exposeInMainWorld('quantDesktop', createBridge())

module.exports = { VERSION_ARG, createBridge, readVersion }
