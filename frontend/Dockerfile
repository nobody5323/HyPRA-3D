# =============================================================
# HyPRA 前端镜像（Next.js 15 App Router）
#   构建：docker compose build frontend
#   运行：node server.js（standalone 产物，容器内端口 3000）
#
# 三阶段构建：
#   deps    只装依赖（package*.json 未变时复用层缓存）
#   builder 编译（产出 .next/standalone + .next/static）
#   runner  仅拷贝 standalone 产物，镜像不含全量 node_modules
# =============================================================

# ---- 阶段 1：安装依赖 ----
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# npm 源可经构建参数覆盖
# （国内网络：--build-arg NPM_REGISTRY=https://registry.npmmirror.com）
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN npm ci --registry="$NPM_REGISTRY"

# ---- 阶段 2：编译 ----
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* 属于**构建期内联**变量：浏览器端的请求地址必须在编译时确定，
# 且必须是「宿主机浏览器可达」的地址（容器名 backend 在浏览器中无法解析），
# 故默认 http://localhost:8000，由 docker-compose 的 build.args 注入。
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---- 阶段 3：运行时（仅 standalone 产物）----
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# 非 root 运行（最小权限）
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

# standalone：自带裁剪后的 node_modules 与 server.js
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
# 静态资源需显式拷贝到 standalone 目录（Next.js 官方约定）
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
