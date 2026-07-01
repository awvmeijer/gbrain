import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Served at / off the capture sidecar (127.0.0.1:8787) behind the Tailscale Funnel.
// Dev server proxies API + capture to the sidecar so `npm run dev` is fully live.
export default defineConfig({
  base: '/',
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8787',
      '/capture': 'http://127.0.0.1:8787',
      '/health': 'http://127.0.0.1:8787',
    },
  },
})
