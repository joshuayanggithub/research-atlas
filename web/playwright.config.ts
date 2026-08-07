import { defineConfig, devices } from "@playwright/test";

// E2E tests drive the real app against the committed static bundle in public/data via the
// Vite dev server. Critical workflows only — browser verification of new visual work is
// still done manually per AGENTS.md, but these guard the stable paths from regressions.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://localhost:4321",
    trace: "on-first-retry",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    // Mobile viewport on Chromium (no extra WebKit download needed for local/CI runs).
    {
      name: "mobile",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
  // Serve the built app on a dedicated port so tests don't collide with `npm run dev`.
  webServer: {
    command: "npm run build && npm run preview -- --port 4321 --strictPort",
    url: "http://localhost:4321",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
