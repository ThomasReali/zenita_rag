import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [tailwindcss()],
  server: {
    // proxy API calls to the FastAPI backend during development
    proxy: { '/api': 'http://localhost:8000' },
  },
})
