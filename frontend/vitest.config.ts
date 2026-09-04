import { defineConfig } from 'vitest/config'
import { resolve } from 'path'

export default defineConfig({
  resolve: {
    alias: {
      '@renderer': resolve(__dirname, 'src/renderer/src'),
      '@': resolve(__dirname, 'src/renderer/src'),
    },
  },
  test: {
    environment: 'node',
    include: ['src/renderer/src/**/*.{test,spec}.ts'],
    globals: false,
  },
})
