import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// base: '/' —— 产物挂载在 FastAPI /app 下
export default defineConfig({
  plugins: [vue()],
  base: '/',
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8766',
      '/thumb': 'http://127.0.0.1:8766',
      '/preview': 'http://127.0.0.1:8766',
    },
  },
})
