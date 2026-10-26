/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.API_PROXY_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react()],
    // Only NEXT_PUBLIC_* variables are embedded in the client bundle.
    envPrefix: 'NEXT_PUBLIC_',
    server: {
      proxy: {
        // The FastAPI backend sends no CORS headers; proxy keeps requests same-origin in dev.
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
    test: {
      environment: 'jsdom',
      include: ['src/**/*.test.{ts,tsx}'],
      restoreMocks: true,
      unstubGlobals: true,
    },
  }
})
