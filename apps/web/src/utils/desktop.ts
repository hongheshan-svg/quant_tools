// Electron 桌面端通过 preload 注入的能力（apps/desktop/preload.js）；在浏览器里打开时为 undefined
export interface DesktopInfo {
  version: string
  dataDir: string
  packaged: boolean
}

export interface QuantDesktop {
  version: string
  info: () => Promise<DesktopInfo>
  openDataDir: () => Promise<string>
  openLogDir: () => Promise<string>
  retry: () => Promise<void>
}

declare global {
  interface Window {
    quantDesktop?: QuantDesktop
  }
}

export const getDesktop = (): QuantDesktop | undefined => window.quantDesktop
