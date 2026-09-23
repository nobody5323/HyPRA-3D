import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // 步 1 的用例都是纯逻辑（窗口几何 / IPC 入参校验），不需要 DOM
    environment: "node",
    include: ["tests/**/*.test.{ts,tsx}"],
    // 与 frontend 保持一致：不开 globals，测试显式 import
    globals: false,
    restoreMocks: true,
    unstubGlobals: true,
  },
});
