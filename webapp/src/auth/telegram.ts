export interface TelegramWebApp {
  initData: string
  ready: () => void
  expand: () => void
  requestFullscreen?: () => void
  showAlert?: (message: string) => void
  themeParams?: Record<string, string>
}

declare global {
  interface Window {
    Telegram?: { WebApp: TelegramWebApp }
  }
}

export function getTelegramWebApp(): TelegramWebApp | null {
  return window.Telegram?.WebApp ?? null
}

export function getInitData(): string | null {
  const tg = getTelegramWebApp()
  if (tg?.initData) return tg.initData
  return sessionStorage.getItem('tg_init_data')
}

export function persistInitData(): void {
  const tg = getTelegramWebApp()
  if (tg?.initData) {
    sessionStorage.setItem('tg_init_data', tg.initData)
  }
}
