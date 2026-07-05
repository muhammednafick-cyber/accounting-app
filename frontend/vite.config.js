import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // Output compiled assets to Flask's static folder
    outDir: '../static/dist',
    emptyOutDir: true,
    manifest: true, // Generate manifest.json for backend integration
    rollupOptions: {
      input: {
        main: 'src/main.jsx',
      },
    },
  },
  server: {
    // Proxy API requests to Flask backend during development
    proxy: {
      '/api': 'http://127.0.0.1:5000',
    },
  },
})
