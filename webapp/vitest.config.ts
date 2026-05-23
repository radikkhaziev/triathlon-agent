import { defineConfig } from 'vitest/config'

// Halo Phase-0: unit tests for pure display logic (utils/recovery.ts).
// Node env — no DOM/React needed. Component tests are out of Phase-0 scope.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
