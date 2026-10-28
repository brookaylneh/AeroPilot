import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  root: 'web',
  plugins: [react()],
  server: {
    port: 5173,
    watch: { ignored: ['**/.local/**', '**/world/unreal/**'] },
    fs: {
      deny: [
        '.env',
        '.env.*',
        '**/*.{crt,pem}',
        '**/.git/**',
        '**/.secrets/**',
        '**/.local/**',
        '**/*.py',
        '**/world/**',
        '**/mission_planner/agents/**',
      ],
    },
    proxy: {
      '/api': { target: 'http://127.0.0.1:3001', changeOrigin: false, ws: true },
      '/pixel-streaming': {
        target: 'ws://127.0.0.1:8080',
        ws: true,
        rewrite: (path) => path.replace(/^\/pixel-streaming/, '') || '/',
      },
    },
  },
  build: { outDir: '../.local/dist', emptyOutDir: true, chunkSizeWarningLimit: 1100 },
});
