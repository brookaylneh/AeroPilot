import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests/browser',
  timeout: 180_000,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:3002',
    viewport: { width: 1440, height: 960 },
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH,
      args: ['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
    },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.SNR_BROWSER_REPORT || 'evidence/browser-results.json' }],
  ],
  webServer: [
    {
      command: 'npm start',
      env: { PORT: '3002', DATA_DIR: process.env.SNR_BROWSER_DATA_DIR || 'data/browser-tests' },
      url: 'http://127.0.0.1:3002/api/health',
      reuseExistingServer: false,
      timeout: 120000,
    },
  ],
});
