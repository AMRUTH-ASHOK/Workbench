import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  use: {
    baseURL: "http://127.0.0.1:4319",
    browserName: "chromium",
    channel: "chrome",
    viewport: { width: 1440, height: 1000 },
  },
  webServer: {
    command: "../.venv/bin/python ../tests/ui_server.py",
    url: "http://127.0.0.1:4319/api/health",
    reuseExistingServer: false,
    timeout: 30000,
  },
  reporter: "list",
});
