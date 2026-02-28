# 懒猫微服CI/CD部署用 - 合并Dockerfile
# 一个镜像包含前端(nginx)+后端(python)，通过不同启动命令运行不同服务
#
# 前端服务: nginx -g 'daemon off;'
# 后端服务: cd /app/web/backend && uvicorn app.main:app ...
# Celery服务: cd /app/web/backend && celery -A app.celery_app worker ...

# ============================================================
# 阶段1：构建前端 React 应用
# ============================================================
FROM crpi-w9u53ghdxy8m3wgg.cn-hangzhou.personal.cr.aliyuncs.com/cocofee2026/node:22-alpine AS frontend-builder

WORKDIR /app

# 先复制依赖文件（利用Docker缓存层）
COPY web/frontend/package*.json ./

# 安装依赖（国内npm镜像加速）
RUN npm ci --registry=https://registry.npmmirror.com

# 复制前端源码并构建
COPY web/frontend/ ./
RUN npm run build

# ============================================================
# 阶段2：最终镜像（Python + Nginx）
# ============================================================
FROM crpi-w9u53ghdxy8m3wgg.cn-hangzhou.personal.cr.aliyuncs.com/cocofee2026/python:3.12-slim

# 设置UTF-8编码（避免中文乱码）
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONIOENCODING=utf-8

WORKDIR /app

# 使用阿里云Debian镜像源（国内网络加速）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; true

# 安装系统依赖（nginx用于前端托管，gcc/libpq-dev用于Python包编译）
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装Python依赖（核心匹配引擎 + Web后端）
COPY requirements.txt /app/requirements.txt
COPY web/backend/requirements.txt /app/web/backend/requirements.txt
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r /app/requirements.txt \
    && pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r /app/web/backend/requirements.txt

# 复制前端构建产物 + nginx配置
COPY --from=frontend-builder /app/dist /usr/share/nginx/html
COPY deploy/lazycat/nginx.conf /etc/nginx/conf.d/default.conf

# 复制后端代码
COPY config.py /app/config.py
COPY main.py /app/main.py
COPY src/ /app/src/
COPY tools/ /app/tools/
COPY tests/ /app/tests/
COPY knowledge/ /app/knowledge/
COPY web/backend/ /app/web/backend/

# 创建必要的目录
RUN mkdir -p /app/output/uploads /app/output/tasks /app/data /app/db /app/logs

EXPOSE 80 8000

# 默认启动nginx（被manifest中的command覆盖）
CMD ["nginx", "-g", "daemon off;"]
