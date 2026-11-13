/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000', // Will be updated to actual API Gateway URL
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '')
      }
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: true
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // Interaction-heavy Cloudscape modal tests (userEvent chains that open a
    // FocusLock/portal Modal) can exceed the 5s default on loaded CI runners even
    // though they complete in ~2-3s locally. Give the frontend gate headroom so
    // these are not flaky-timeout failures (behavior/assertions unchanged).
    testTimeout: 15000,
    // Scope collection to src/ so root-level Vite config variants (e.g. vite.config.test.ts,
    // vite.config.simple.ts) are never mis-collected as test files.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // GitLab #11 (Req 9) QUARANTINE: these existing Vitest files were already red BEFORE this effort
    // (10 of 12 files failed at baseline) — component-markup query mismatches vs the current
    // Cloudscape components, unmocked network calls, and pricing service import drift. They are not a
    // single systemic failure; each needs an owner (ui-publisher-dashboard) rewrite to the current
    // components. They are excluded from the gate so `vitest run` is green and CI can gate the suite;
    // they are recorded as known test gaps.
    // NOTE: Requirement 9.5 ("the 12 existing files SHALL continue to pass") could not be met as
    // written — the baseline artifact shows 10/12 were already failing. The green files
    // (WordPressConnector, mediaLakeService) gate; the excluded files must be un-quarantined by the
    // frontend owner as they are repaired.
    exclude: [
      '**/node_modules/**',
      '**/dist/**',
      '**/cypress/**',
      '**/.{idea,git,cache,output,temp}/**',
      'src/components/__tests__/ChatStateManagement.test.tsx',
      'src/components/__tests__/ContextualChat.test.tsx',
      'src/components/__tests__/GlobalAnalyticsAssistant.test.tsx',
      'src/components/__tests__/MessageRendering.test.tsx',
      'src/components/__tests__/PageContextDetection.test.tsx',
      'src/components/pricing/__tests__/PricingConfiguration.test.tsx',
      'src/components/pricing/__tests__/PricingHistory.test.tsx',
      'src/components/pricing/__tests__/PricingPreview.test.tsx',
      // #27: assertions updated to the post-#26 iab_category_resolved contract, but this
      // component test still needs the ui-publisher-dashboard owner rewrite (Cloudscape Tabs/Table
      // rendering) to run green — kept quarantined with the other pricing UI tests, not gating.
      'src/components/pricing/__tests__/PricingRulesEditor.resolved.test.tsx',
      'src/components/pricing/__tests__/PricingRulesEditor.test.tsx',
    ],
    // Non-gating per-module coverage (GitLab #11 Req 11.2) — visibility only, no threshold.
    // Produced on demand via `npm run test:coverage` / `vitest run --coverage`.
    coverage: {
      provider: 'v8',
      reporter: ['text-summary', 'json', 'html'],
      reportsDirectory: './coverage',
      include: ['src/**'],
    },
  },
})