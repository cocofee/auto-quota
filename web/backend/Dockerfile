# auto-quota 后端 Dockerfile
#
# 构建上下文是项目根目录（需要访问 src/, config.py, main.py 等核心代码）。
# 同一个镜像用于 FastAPI 和 Celery Worker（通过不同的启动命令区分）。
#
# 构建命令（由 docker-compose 自动调用）:
#   docker build -f web/backend/Dockerfile -t autoquota-backend .

ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

# UTF-8编码（镜像层级写死，解决celery worker中文编码ascii错误）
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONIOENCODING=utf-8

# 设置工作目录
WORKDIR /app

# 使用阿里云Debian镜像（国内网络更稳定）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; true

# 安装系统依赖（PostgreSQL客户端库、构建工具等）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 缓存层（依赖不变时跳过安装）
# Docker用精简版（无torch/transformers/chromadb，镜像从13GB降到~1GB）
COPY requirements-docker.txt /app/requirements.txt
COPY web/backend/requirements.txt /app/web/backend/requirements.txt

# 安装 Python 依赖（核心匹配引擎 + Web后端，使用国内PyPI镜像加速）
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r /app/requirements.txt \
    && pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r /app/web/backend/requirements.txt

# 复制项目代码
# 核心匹配逻辑（src/, config.py, main.py 等）
COPY config.py /app/config.py
COPY main.py /app/main.py
COPY docs/ /app/docs/
COPY db/__init__.py /app/db/__init__.py
COPY db/sqlite.py /app/db/sqlite.py
COPY src/ /app/src/
COPY tools/ /app/tools/
COPY tests/ /app/tests/
COPY knowledge/ /app/knowledge/

# Web后端代码
COPY web/backend/ /app/web/backend/

# 创建必要的目录
RUN mkdir -p /app/output/uploads /app/output/tasks /app/data /app/db /app/logs

# 默认端口（FastAPI）
EXPOSE 8000

# 默认启动命令（被 docker-compose 覆盖）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
