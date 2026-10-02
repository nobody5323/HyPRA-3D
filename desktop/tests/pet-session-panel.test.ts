import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * 桌宠窗的「角色与会话」入口（接线回归用例）。
 *
 * 这里断言**源码接线**而不是渲染结果：desktop 的 vitest 跑在 node 环境、
 * 没有 DOM 基建（见 vitest.config.mts），与 `memory-panel.test.ts` /
 * `pet-window-size.test.ts` 同一做法。
 *
 * 守住的教训：桌宠窗是长期开着的那个窗口，但换角色与翻历史会话原本只能去
 * 控制台或 Web 端——用户在这里找不到入口，现场反馈就是「不知道怎么切换会话」。
 * 这组用例把「入口存在」与「它真的接到会话 hook 上」钉住，顺带守住两条边界：
 * 不引 Tailwind 类（桌宠入口没加载 Tailwind）、不写死角色名。
 */

const PET_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "../src/renderer/pet");

const panel = fs.readFileSync(path.join(PET_DIR, "SessionPanel.tsx"), "utf8");
const settings = fs.readFileSync(path.join(PET_DIR, "SettingsPanel.tsx"), "utf8");
const app = fs.readFileSync(path.join(PET_DIR, "App.tsx"), "utf8");

describe("桌宠窗的角色与会话入口", () => {
  it("会话面板把切换 / 新建 / 打开 / 删除 / 清空五件事都接了出来", () => {
    for (const call of [
      "selectPersona",
      "newSession",
      "openSession",
      "removeSession",
      "clearAll",
    ]) {
      expect(panel).toContain(call);
    }
  });

  it("这些能力真的接到 useChatSession 上（而不是空壳 prop）", () => {
    for (const wire of [
      "selectPersona: session.setPersonaId",
      "openSession: session.openSession",
      "newSession: session.newSession",
      "removeSession: session.removeSession",
      "clearAll: session.clearAllSessions",
      "catalog: personaCatalog",
      "personaId: session.personaId",
      "sessionId: session.sessionId",
    ]) {
      expect(app).toContain(wire);
    }
    expect(settings).toContain("<SessionPanel");
  });

  it("对话进行中禁用切换与删除（避免与正在跑的回合并发改状态）", () => {
    expect(panel).toContain("busy");
    expect(app).toContain("busy: session.busy");
    // 禁用必须落到控件上，不能只算出变量不用
    expect(panel).toContain("disabled={blocked}");
  });

  it("一次要够多会话（后端默认只回 20 条，少报会让用户以为删不完）", () => {
    expect(panel).toContain("limit: 100");
  });

  it("当前会话靠 sessionId 命中高亮", () => {
    expect(panel).toContain("item.session_id === sessionId");
    expect(panel).toContain('data-current=');
  });

  it("用 pet.css 的类，不引 Tailwind 工具类（桌宠入口没加载 Tailwind）", () => {
    for (const forbidden of [
      'className="rounded',
      "text-xs",
      "border-line",
      "bg-surface",
      "focus-ring",
    ]) {
      expect(panel).not.toContain(forbidden);
    }
    expect(panel).toContain("petField");
    expect(panel).toContain("petSessionList");
  });

  it("不写死角色名（角色名一律来自人设清单）", () => {
    expect(panel).not.toContain("苏澄");
    expect(panel).not.toContain("小林");
    expect(panel).toContain("personaName(");
  });
});
