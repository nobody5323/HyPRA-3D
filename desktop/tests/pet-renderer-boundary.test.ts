import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * 桌宠渲染层的两条边界（都对应真实会踩到的坑）。
 *
 * 1. **不引入 Web 端的 Tailwind 组件**：`pet/main.tsx` 只加载 `pet.css`，
 *    Tailwind 接在**控制台入口**（`console/main.tsx` 引 `globals.css`）。
 *    在桌宠窗里 import `@/components/*`（那些组件用 Tailwind 类写样式）不会报错，
 *    但会渲染成没有样式的裸元素——所以样式相关的复用必须走 `.petField` 体系。
 * 2. **用户称呼接后端共享偏好**：写死的默认人名（曾经是 `const USER_NAME = "小林"`）
 *    会让用户改了称呼后桌宠窗还叫旧的。值来自 `session.userName`，保存走
 *    `session.setUserName`。
 */

const PET_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "../src/renderer/pet");

function petSources(): string[] {
  return fs.readdirSync(PET_DIR).filter((name) => /\.tsx?$/.test(name));
}

/**
 * 收集**运行时**会用到的模块路径。
 *
 * 先剥注释（注释里可能出现示例 import），再剔掉 `import type`（编译后完全擦除）——
 * 与 `preload-imports.test.ts` 同一套做法。
 */
function runtimeImports(source: string): string[] {
  const withoutComments = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  const withoutTypeImports = withoutComments.replace(
    /import\s+type\s[\s\S]*?from\s+["'][^"']+["'];?/g,
    "",
  );
  return [...withoutTypeImports.matchAll(/from\s+["']([^"']+)["']|import\s+["']([^"']+)["']/g)]
    .map((match) => match[1] ?? match[2])
    .filter((spec): spec is string => Boolean(spec));
}

describe("桌宠渲染层的样式边界", () => {
  it("不引入 Web 端的 Tailwind 组件（桌宠入口没有加载 Tailwind）", () => {
    const offenders = petSources().filter((name) =>
      runtimeImports(fs.readFileSync(path.join(PET_DIR, name), "utf8")).some((spec) =>
        spec.startsWith("@/components/"),
      ),
    );

    expect(offenders).toEqual([]);
  });
});

describe("桌宠设置里的用户称呼", () => {
  const panel = fs.readFileSync(path.join(PET_DIR, "SettingsPanel.tsx"), "utf8");
  const app = fs.readFileSync(path.join(PET_DIR, "App.tsx"), "utf8");

  it("用共享的行为工具（去空白 / 长度上限与另两端同一口径）", () => {
    expect(panel).toContain("normalizeUserName");
    expect(panel).toContain("USER_NAME_MAX_LENGTH");
  });

  it("值来自当前会话、保存走共享偏好，而不是写死的常量", () => {
    expect(app).toContain("userName={session.userName}");
    expect(app).toContain("onSaveUserName={session.setUserName}");
  });
});
