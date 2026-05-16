import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { execSync } from 'node:child_process'

function safeShell(cmd: string, fallback: string): string {
  try {
    return execSync(cmd, { stdio: ['ignore', 'pipe', 'ignore'] })
      .toString()
      .trim()
  } catch {
    return fallback
  }
}

const GIT_SHA =
  process.env.VITE_GIT_SHA ||
  safeShell('git rev-parse --short HEAD', 'dev')
const BUILD_TIME =
  process.env.VITE_BUILD_TIME || new Date().toISOString()

export default defineConfig({
  plugins: [react()],
  define: {
    'import.meta.env.VITE_GIT_SHA': JSON.stringify(GIT_SHA),
    'import.meta.env.VITE_BUILD_TIME': JSON.stringify(BUILD_TIME),
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
