import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Simple Vite config for quick deployment
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      onwarn(warning, warn) {
        // Suppress TypeScript warnings for quick build
        if (warning.code === 'UNUSED_EXTERNAL_IMPORT') return
        if (warning.code === 'CIRCULAR_DEPENDENCY') return
        warn(warning)
      }
    }
  },
  esbuild: {
    // Ignore TypeScript errors for quick build
    logOverride: { 'this-is-undefined-in-esm': 'silent' }
  }
})