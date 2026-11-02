import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';
export default defineConfig({
  root: 'web',
  plugins: [
    react(),
    {
      name: 'csrt-runtime',
      generateBundle() {
        for (const name of ['csrt.js', 'csrt.wasm', 'OPENCV-LICENSE.txt'])
          this.emitFile({
            type: 'asset',
            fileName: `csrt/${name}`,
            source: readFileSync(new URL(`./.local/vendor/csrt/runtime/${name}`, import.meta.url)),
          });
      },
    },
  ],
  worker: { format: 'es' },
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
        '**/world/!(cosys)/**',
        '**/world/cosys/!(world.json)',
        '**/world/cosys/*/**',
        '**/mission_planner/agents/**',
      ],
    },
  },
  build: { outDir: '../.local/dist', emptyOutDir: true, chunkSizeWarningLimit: 1100 },
});
