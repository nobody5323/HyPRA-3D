import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const currentDir = path.dirname(fileURLToPath(import.meta.url));

/**
 * Cubism 运行时桥的双候选解析。
 *
 * 与 `tsconfig.json` 的 `paths` 保持同一套约定：
 * 装了 SDK → 用 `vendor/cubism/bridge.ts`；没装 → 回落提交入库的 unavailable 实现。
 * 这样「参赛仓库不带 SDK」与「本地装了 SDK」两种情况都能 build 通过。
 */
const CUBISM_BRIDGE_ALIAS = "@cubism-bridge";
const CUBISM_BRIDGE_REAL = path.join(currentDir, "vendor/cubism/bridge.ts");
const CUBISM_BRIDGE_FALLBACK = path.join(currentDir, "lib/live2d/cubism-bridge.unavailable.ts");
const cubismInstalled = fs.existsSync(CUBISM_BRIDGE_REAL);

/** @type {import('next').NextConfig} */
const nextConfig = {
  // standalone：供 Docker 多阶段构建只拷贝裁剪后的产物（约 200MB，而非全量 node_modules）
  // 不影响 npm run dev / next start 的本地行为
  output: "standalone",
  // 后端 API 地址（浏览器发起请求，需填**宿主机可达**地址，如 http://localhost:8000）
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000",
  },
  eslint: {
    // 参赛演示优先保证构建可复现；lint 单独跑
    ignoreDuringBuilds: true,
  },
  webpack: (config) => {
    // 数组形式 = 按顺序取第一个能解析的路径（webpack 原生支持）
    config.resolve.alias[CUBISM_BRIDGE_ALIAS] = cubismInstalled
      ? [CUBISM_BRIDGE_REAL, CUBISM_BRIDGE_FALLBACK]
      : CUBISM_BRIDGE_FALLBACK;
    return config;
  },
  // Turbopack 的 resolveAlias 不支持数组，只能按构建时是否存在 SDK 二选一
  turbopack: {
    resolveAlias: {
      [CUBISM_BRIDGE_ALIAS]: cubismInstalled
        ? "./vendor/cubism/bridge.ts"
        : "./lib/live2d/cubism-bridge.unavailable.ts",
    },
  },
};

export default nextConfig;
