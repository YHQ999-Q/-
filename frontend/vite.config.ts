import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
// @ts-ignore JavaScript server-only middleware.
import { beeApi } from './server/vite-api.mjs'

export default defineConfig({
  plugins: [react(), beeApi()],
  server: {
    proxy: {
      '/opentutor-api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/opentutor-api/, ''),
      },
      '/teach-api': {
        target: 'http://127.0.0.1:5001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/teach-api/, '/api'),
      },
    },
  },
})
