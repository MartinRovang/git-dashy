import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { dashyMock } from './mock/api.ts'

// https://vite.dev/config/ and https://v2.tauri.app/start/frontend/vite/
export default defineConfig({
  plugins: [react(), dashyMock()],
  // Tauri's own output is noisy; keep the Rust warnings readable.
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ['**/src-tauri/**'] },
    // dev: run `gitdashy --no-open --demo --port 7777` and open http://localhost:1420/?token=…
    proxy: { '/api': 'http://127.0.0.1:7777' },
  },
})
