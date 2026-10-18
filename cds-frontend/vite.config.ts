/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 本地开发道：默认打 docker 里的 cds-api:8000；改后端时把 CDS_API_TARGET 指到本机 uvicorn（:8001），
// 就不必每改一次重打一次镜像。生产/镜像面走 nginx.conf，与本文件无关。
const API_TARGET = process.env.CDS_API_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        // JupyterLab kernel 通道是 WebSocket（/api/jupyter/api/kernels/*/channels），
        // 不升级转发则开发环 iframe 只渲壳不连核。
        ws: true,
      },
      '/open': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // 16 worker（16 核机器的默认值）会让最重的几个页面用例越过硬编码的 5s testTimeout：
    // 同一棵代码树三次带负载跑分别红 2/4/1 条，全部是 `Test timed out in 5000ms`、
    // 0 条断言错，单跑这些文件只要 1.6–1.8s。限到 4 worker 后全量 308/308 绿、
    // 耗时 84.24s（16 worker 是 80.63s）——墙钟时间没付代价。证据：.cds-e2e/vitest-4w.log。
    maxWorkers: 4,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/test/**', 'src/**/*.d.ts', 'src/main.tsx', 'src/vite-env.d.ts'],
    },
  },
})
