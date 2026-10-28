import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    watch: {
      ignored: ['**/vendor/**', '**/unreal/**', '**/evidence/**', '**/data/**'],
    },
    fs: {
      deny: [
        '.env',
        '.env.*',
        '**/*.{crt,pem}',
        '**/.git/**',
        '**/.secrets/**',
        '**/data/**',
        '**/evidence/**',
        '**/vendor/**',
        '**/*.py',
        '**/world/unreal/**',
        '**/world/scripts/**',
        '**/world/simulator/**',
        '**/mission_control/services/**',
        '**/docs/**',
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
  build: { chunkSizeWarningLimit: 1100 },
});
