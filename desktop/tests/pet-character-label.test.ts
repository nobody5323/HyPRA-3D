import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * 桌宠窗的角色名接线（回归用例）。
 *
 * 桌宠窗曾经把角色名写成 `const CHARACTER_LABEL = "苏澄"`。内置人设只是示例之一，
 * 写死之后用户换成自建角色，气泡、会话标签、本地渲染层的水印都还自称「苏澄」。
 *
 * 与 `memory-panel.test.ts` 同一做法：desktop 的 vitest 跑在 node 环境、
 * 没有 DOM 基建，所以断言的是源码接线而不是渲染结果。
 */

const APP_PATH = fileURLToPath(new URL("../src/renderer/pet/App.tsx", import.meta.url));

const source = readFileSync(APP_PATH, "utf8");

/**
 * 只检查真会渲染出去的代码。
 *
 * 注释里提到角色名（解释这段历史）不算硬编码，所以先剥掉注释再扫。
 */
function stripComments(code: string): string {
  return code.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

describe("桌宠角色名", () => {
  it("跟着当前人设走（清单 + session.personaId），而不是写死的常量", () => {
    expect(source).toContain("personaName(personaCatalog, session.personaId)");
    expect(source).not.toContain("CHARACTER_LABEL");
  });

  it("源码里不出现具体角色名与用户称呼", () => {
    const code = stripComments(source);
    expect(code).not.toContain("苏澄");
    expect(code).not.toContain("小林");
  });
});
