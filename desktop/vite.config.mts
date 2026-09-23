import fs from "node:fs";
import { cp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import type { Plugin } from "vite";

const currentDir = path.dirname(fileURLToPath(import.meta.url));

/**
 * 复用 Web 端（Next.js）的前端代码。
 *
 * 桌宠窗与 Web 端共用同一套渲染器 / hooks / 接口封装（`frontend/lib`、`frontend/hooks`），
 * 因此这里把 `@/` 指到 `frontend/`——与 Web 端 tsconfig 的 `paths` 保持同一套约定，
 * 复用的文件一行都不用改。
 */
const frontendDir = path.resolve(currentDir, "../frontend");

/**
 * Cubism 运行时桥的**双候选解析**（与 Web 端 next.config.mjs 同一套规则）：
 * 装了 SDK → 用 `vendor/cubism/bridge.ts`（真实实现）；
 * 没装 → 回落提交入库的 unavailable 实现，构建依然能过。
 */
const CUBISM_BRIDGE_ALIAS = "@cubism-bridge";
const CUBISM_BRIDGE_REAL = path.join(frontendDir, "vendor/cubism/bridge.ts");
const CUBISM_BRIDGE_FALLBACK = path.join(frontendDir, "lib/live2d/cubism-bridge.unavailable.ts");
const cubismInstalled = fs.existsSync(CUBISM_BRIDGE_REAL);

/**
 * 允许进入发布包的 Web 端公共资源（**白名单**）。
 *
 * 硬性边界：`live2d/` 模型、`*.moc3`、声音模型等一律不进包（授权与体积原因），
 * 因此这里不能用 `copyPublicDir: true` 整目录复制。
 */
const RELEASE_PUBLIC_ENTRIES = ["portraits", "vendor/cubism"];

/** 后端地址：与主进程的 `HYPRA_API_BASE` 用同一个变量，避免两处写法不一致 */
const apiBase = process.env.HYPRA_API_BASE ?? "http://localhost:8000";

/**
 * 构建后按白名单把 Web 端 `public/` 里的框架资源拷进产物目录。
 *
 * 生产模式下页面由主进程的 loopback 静态服务提供，资源引用是绝对路径
 * （`/portraits/happy.svg`、`/vendor/cubism/Core/...`），所以它们必须躺在
 * 产物根目录下。
 */
function copyReleasePublicAssets(): Plugin {
  return {
    name: "hypra-copy-release-public-assets",
    apply: "build",
    async closeBundle() {
      const target = path.resolve(currentDir, "dist/renderer");

      for (const entry of RELEASE_PUBLIC_ENTRIES) {
        const source = path.join(frontendDir, "public", entry);

        if (!fs.existsSync(source)) {
          // 未安装 Cubism SDK 时不拷 vendor（运行时会降级到静态立绘）
          console.log(`[desktop] 跳过不存在的公共资源：${entry}`);
          continue;
        }

        await cp(source, path.join(target, entry), { recursive: true, force: true });
      }

      // 双保险：万一将来有人改了上面的白名单，也不让模型混进发布包
      await rm(path.join(target, "live2d"), { recursive: true, force: true });
    },
  };
}

export default defineConfig({
  plugins: [react(), copyReleasePublicAssets()],
  // 页面可能以 file:// 之外的相对路径加载，静态资源必须走相对路径
  base: "./",
  /*
   * 开发期直接复用 Web 端的 `public/`：
   * 前端资源引用是绝对路径（/live2d、/portraits、/vendor/cubism），
   * dev server 从同一个根提供，行为与 Web 端完全一致。
   * 生产构建由上面的插件按白名单拷贝（copyPublicDir 关闭）。
   */
  publicDir: path.join(frontendDir, "public"),
  define: {
    // 复用层（frontend/lib/api/client.ts）用这个变量找后端
    "process.env.NEXT_PUBLIC_API_BASE": JSON.stringify(apiBase),
  },
  resolve: {
    alias: {
      "@": frontendDir,
      [CUBISM_BRIDGE_ALIAS]: cubismInstalled ? CUBISM_BRIDGE_REAL : CUBISM_BRIDGE_FALLBACK,
    },
    /*
     * 强制 React 单实例。
     *
     * 复用层（frontend/）自己有 node_modules，如果不 dedupe，
     * frontend 的文件会解析到**另一份** react：两个副本各有自己的 hooks dispatcher，
     * 症状是渲染层直接报 `Cannot read properties of null (reading 'useState')`。
     */
    dedupe: ["react", "react-dom"],
  },
  build: {
    outDir: "dist/renderer",
    emptyOutDir: true,
    // public/ 由白名单插件拷贝，不能整目录复制（否则本地模型会被打进包）
    copyPublicDir: false,
    rollupOptions: {
      input: {
        pet: path.resolve(currentDir, "pet.html"),
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
  },
});
