import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import './theme'
import './styles.css'

createApp(App).use(router).mount('#app')

// PWA：Service Worker（仅安全上下文注册；自签证书等环境失败则静默降级为普通网页）
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    const secure = location.protocol === 'https:' || ['localhost', '127.0.0.1'].includes(location.hostname)
    if (secure) navigator.serviceWorker.register('/sw.js').catch(() => { /* 环境不支持 → 普通网页 */ })
  })
}
