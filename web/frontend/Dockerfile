# auto-quota frontend Dockerfile
#
# Two-stage build:
# 1. Use Node.js to compile TypeScript and bundle the React app
# 2. Use Nginx to serve static assets and proxy /api requests
#
# Local build example:
#   docker build -t autoquota-frontend web/frontend/

ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginx:alpine
ARG BACKEND_UPSTREAM=backend.cloud.lazycat.app.autoquota.lzcapp:8000

# ============================================================
# Stage 1: build the React application
# ============================================================
FROM ${NODE_IMAGE} AS builder

WORKDIR /app

# Copy dependency manifests first to maximize layer cache hits
COPY package.json package-lock.json ./

# Use the mirror already used by this repo for faster, more stable installs
RUN npm ci --registry=https://registry.npmmirror.com

# Build the production bundle
COPY . .
RUN npm run build

# ============================================================
# Stage 2: serve static assets with Nginx
# ============================================================
FROM ${NGINX_IMAGE}

ARG BACKEND_UPSTREAM

COPY --from=builder /app/dist /usr/share/nginx/html

# Default upstream targets Lazycat. Local builds override it with BACKEND_UPSTREAM=backend:8000.
COPY nginx.conf /tmp/default.conf.template
RUN sed "s|__BACKEND_UPSTREAM__|${BACKEND_UPSTREAM}|g" /tmp/default.conf.template > /etc/nginx/conf.d/default.conf \
    && rm -f /tmp/default.conf.template

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
