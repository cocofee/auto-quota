# auto-quota 前端 Dockerfile
#
# 两阶段构建：
# 1. 构建阶段：用 Node.js 编译 TypeScript + 打包 React
# 2. 运行阶段：用 Nginx 托管静态文件 + 反向代理 API
#
# 构建命令（由 docker-compose 自动调用）:
#   docker build -t autoquota-frontend web/frontend/

# ============================================================
# 阶段1：构建 React 应用
# ============================================================
FROM dockerpull.org/node:22-alpine AS builder

WORKDIR /app

# 先复制依赖文件（利用 Docker 缓存层）
COPY package.json package-lock.json ./

# 安装依赖（使用国内npm镜像加速）
RUN npm ci --registry=https://registry.npmmirror.com

# 复制源码并构建
COPY . .
RUN npm run build

# ============================================================
# 阶段2：Nginx 托管
# ============================================================
FROM dockerpull.org/nginx:alpine

# 复制构建产物到 Nginx 默认目录
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 Nginx 配置（反向代理 /api 到后端）
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Nginx 默认端口
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
