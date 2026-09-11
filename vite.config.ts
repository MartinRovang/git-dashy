import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/ and https://v2.tauri.app/start/frontend/vite/
export default defineConfig({
  plugins: [react()],
  // Tauri's own output is noisy; keep the Rust warnings readable.
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ['**/src-tauri/**'] },
  },
})
