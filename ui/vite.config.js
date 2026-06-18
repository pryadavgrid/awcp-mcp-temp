import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server is launched by scripts/run_everything.sh as
//   node_modules/.bin/vite --host --port 5173
// with VITE_API_BASE pointing at the gateway. Nothing here touches the backend.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: true },
})
