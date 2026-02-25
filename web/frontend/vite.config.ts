import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Vite 开发服务器配置
 *
 * server.proxy：开发时将 /api 请求转发到后端（FastAPI 8000端口），
 * 避免跨域问题，生产环境由 Nginx 代理。
 */
export default defineConfig({
  plugins: [react()],
  build: {
    // 每次构建使用时间戳做文件名后缀，防止浏览器缓存旧版本
    rollupOptions: {
      output: {
        entryFileNames: `assets/[name]-[hash]-${Date.now()}.js`,
        chunkFileNames: `assets/[name]-[hash].js`,
        assetFileNames: `assets/[name]-[hash].[ext]`,
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
