import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  root: 'web',
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    strictPort: true,
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
  },
  build: { outDir: '../.local/dist', emptyOutDir: true, chunkSizeWarningLimit: 1100 },
});
