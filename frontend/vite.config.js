import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Forward /api/* to the FastAPI server. Without this the browser would see
    // requests from :5173 to :8000 as cross-origin; with it, the frontend only
    // ever talks to its own origin and Vite relays the call. It also keeps the
    // fetch URLs ('/api/search') correct in production, where the built files
    // would be served from the same origin as the API.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
