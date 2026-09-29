// Electron 桌面端通过 preload 注入的能力（apps/desktop/preload.js）；在浏览器里打开时为 undefined
export interface DesktopInfo {
  version: string
  dataDir: string
  packaged: boolean
}

export interface UpdateCheckResult {
  status: 'available' | 'latest' | 'error' | 'unsupported'
  version?: string
  message: string
}

export interface DesktopPrefs {
  autoCheckUpdates?: boolean
  lastCheck?: number
}

export interface QuantDesktop {
  version: string
  info: () => Promise<DesktopInfo>
  openDataDir: () => Promise<string>
  openLogDir: () => Promise<string>
  retry: () => Promise<void>
  // 以下为自动更新相关，旧版桌面端没有
  checkForUpdates?: () => Promise<UpdateCheckResult>
  getPrefs?: () => Promise<DesktopPrefs>
  setPrefs?: (prefs: DesktopPrefs) => Promise<DesktopPrefs>
}

declare global {
  interface Window {
    quantDesktop?: QuantDesktop
  }
}

export const getDesktop = (): QuantDesktop | undefined => window.quantDesktop
