import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

const DEV_PROXY_BASE = '/proxy/3000/'

function strippedProxyPathCompat(): Plugin {
  return {
    name: 'stripped-proxy-path-compat',
    configureServer(server) {
      // The workspace gateway exposes /proxy/3000/* to the browser but strips
      // that prefix before forwarding to Vite. Vite still needs the explicit
      // public base when generating browser URLs, so restore it internally for
      // non-API requests. Supporting both shapes also keeps direct/local QA
      // deterministic and avoids coupling to one gateway implementation.
      server.middlewares.use((request, _response, next) => {
        const url = request.url || '/'
        if (url.startsWith(`${DEV_PROXY_BASE}api`)) {
          request.url = url.slice(DEV_PROXY_BASE.length - 1)
        } else if (!url.startsWith(DEV_PROXY_BASE) && !url.startsWith('/api')) {
          request.url = `${DEV_PROXY_BASE.slice(0, -1)}${url}`
        }
        next()
      })
    },
  }
}

export default defineConfig(({ command }) => ({
  plugins: [strippedProxyPathCompat(), react()],
  // The workspace exposes dev servers below /proxy/<port>/. An explicit dev
  // base keeps Vite's injected client, React refresh runtime and source entry
  // under that prefix instead of incorrectly requesting them from `/`.
  // Production builds remain location-independent for Nginx/static hosting.
  base: command === 'serve' ? DEV_PROXY_BASE : './',
  server: {
    port: 3000,
    host: '0.0.0.0',
    allowedHosts: ['5665bc99-279b-4edf-8553-c7b7804c6e02-vscode-zw05.mlp.sankuai.com'],
    proxy: {
      '/api': {
        target: 'http://localhost:8100',
        changeOrigin: true,
      }
    }
  },
  preview: {
    port: 3000,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: 'http://localhost:8100',
        changeOrigin: true,
      }
    }
  }
}))
