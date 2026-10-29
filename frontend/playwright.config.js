import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  timeout: 30000,
  reporter: [['list'], ['json', { outputFile: '../examples/ui/browser-tests.json' }]],
  use: {
    baseURL: 'http://127.0.0.1:8765',
    browserName: 'chromium',
    ...(process.platform === 'win32' ? { channel: 'msedge' } : {}),
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: [
    { command: 'python ../run.py serve --port 8765 --timeout 20', url: 'http://127.0.0.1:8765/api/config', reuseExistingServer: !process.env.CI },
    { command: 'node tests/fixture-server.mjs', url: 'http://127.0.0.1:8766/api/config', reuseExistingServer: !process.env.CI },
  ],
})
