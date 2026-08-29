// 主题：auto（跟随系统）/ light / dark，localStorage 持久化
export type Theme = 'auto' | 'light' | 'dark'
const KEY = 'lsTheme'

export function applyTheme(theme: Theme) {
  if (theme === 'auto') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = theme
  localStorage.setItem(KEY, theme)
}

export function currentTheme(): Theme {
  return (localStorage.getItem(KEY) as Theme) || 'auto'
}

// 应用启动时立即生效（main.ts 在 mount 前调用）
applyTheme(currentTheme())
