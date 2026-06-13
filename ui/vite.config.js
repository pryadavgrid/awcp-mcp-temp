import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dashboard is a plain SPA. It talks to the AWCP gateway over HTTP/CORS at
// VITE_API_BASE (default http://localhost:8000). Nothing about the agents or
// tools is configured here — that all comes from the gateway at runtime.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: true },
})
