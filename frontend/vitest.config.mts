import path from "path";
import { fileURLToPath } from "url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const rootDir = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    // 与 tsconfig.json 的 paths 保持一致（`@/*` → 前端根目录）
    alias: { "@": rootDir },
  },
  test: {
    // 组件/hook 测试需要 DOM（jsdom）；纯逻辑测试用同一环境，配置更简单
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    // 不开启 globals：测试显式 import { describe, it, expect }，依赖关系更清楚
    globals: false,
    // 每个用例结束后自动还原 vi.stubGlobal / vi.spyOn，避免用例间互相污染
    restoreMocks: true,
    unstubGlobals: true,
  },
});
