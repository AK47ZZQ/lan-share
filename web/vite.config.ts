import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// base: '/' —— 产物挂载在 FastAPI /app 下
export default defineConfig({
  plugins: [vue()],
  base: '/',
  // 显式声明（9.9 复审 P3）：Vite 当前默认不生成 sourcemap，但钉死以防
  // 升级变默认/本地调试开关误入生产 dist 泄漏源码
  build: { sourcemap: false },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8766',
      '/thumb': 'http://127.0.0.1:8766',
      '/preview': 'http://127.0.0.1:8766',
    },
  },
})
