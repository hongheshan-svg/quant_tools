const assert = require('node:assert/strict')
const Module = require('node:module')
const { test } = require('node:test')

// preload.js 在 Electron 里运行，这里替换掉 electron 模块
const exposed = {}
const invoked = []
const fakeElectron = {
  contextBridge: { exposeInMainWorld: (key, api) => { exposed[key] = api } },
  ipcRenderer: { invoke: (...args) => { invoked.push(args); return Promise.resolve(null) } },
}
const originalLoad = Module._load
Module._load = function load(request, ...rest) {
  return request === 'electron' ? fakeElectron : originalLoad.call(this, request, ...rest)
}
const preload = require('../preload')
Module._load = originalLoad

test('从启动参数读取桌面端版本', () => {
  assert.equal(preload.readVersion(['electron', '--quant-desktop-version=1.2.3']), '1.2.3')
  assert.equal(preload.readVersion(['electron']), '')
})

test('向页面暴露 quantDesktop，并通过 IPC 调用主进程', async () => {
  const api = exposed.quantDesktop
  assert.ok(api)
  await api.openDataDir()
  await api.openLogDir()
  await api.retry()
  await api.info()
  assert.deepEqual(invoked, [
    ['desktop:open-path', 'data'],
    ['desktop:open-path', 'logs'],
    ['desktop:retry'],
    ['desktop:info'],
  ])
})
