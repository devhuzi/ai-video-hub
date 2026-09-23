import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    plugins: [react()],
    server: {
      // In dev, forward /api to a locally running backend (same port as deploy/supervisord.conf).
      proxy: {
        '/api': env.DEV_API_PROXY || 'http://127.0.0.1:8001',
      },
    },
    build: {
      outDir: 'build',
      commonjsOptions: {
        transformMixedEsModules: true,
      }
    },
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    define: {
      'process.env.REACT_APP_BACKEND_URL': JSON.stringify(process.env.REACT_APP_BACKEND_URL || env.REACT_APP_BACKEND_URL || "")
    }
  }
})
