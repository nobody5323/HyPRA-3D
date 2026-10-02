import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * 控制台的会话删除接线（回归用例）。
 *
 * 这里断言的是**源码接线**，不是渲染结果：控制台是 Vite 独立入口，
 * desktop 的 vitest 跑在 node 环境、没有 DOM 基建（见 vitest.config.mts），
 * 与 `pet-window-size.test.ts` / `preload-imports.test.ts` 同一做法。
 *
 * 守住的教训：`SessionList` 只是列表视图，**它自己不调删除接口**；
 * 真实调用方是 `useChatSession`（Web / 桌宠）与 `MemoryPanel`（控制台）。
 * 曾经 `MemoryPanel` 的 `onRemove` 只自增 version 刷新列表、从不发请求，
 * 现场表现就是「点了确认删除，列表刷新一遍，会话还在」。
 */

const PANEL_PATH = fileURLToPath(
  new URL("../src/renderer/console/panels/MemoryPanel.tsx", import.meta.url),
);

const source = readFileSync(PANEL_PATH, "utf8");

/** 抠出 `onRemove={...}` 的回调体（到配对的 `}}` 为止） */
function onRemoveBody(code: string): string {
  const start = code.indexOf("onRemove={");
  if (start === -1) throw new Error("MemoryPanel 里找不到 SessionList 的 onRemove");
  const end = code.indexOf("\n          }}", start);
  if (end === -1) throw new Error("onRemove 回调体的结束位置对不上（缩进变了？）");
  return code.slice(start, end);
}

describe("控制台删除会话", () => {
  it("onRemove 真的发删除请求（而不是只刷新列表）", () => {
    const body = onRemoveBody(source);
    expect(body).toContain("deleteSession(sessionId, personaId)");
  });

  it("删完通知列表刷新", () => {
    expect(onRemoveBody(source)).toContain("setVersion(");
  });

  it("从 api client 导入删除接口（单条 + 按角色清空）", () => {
    expect(source).toContain("deleteSession");
    expect(source).toContain("deleteSessions");
    expect(source).toContain('from "@/lib/api/client"');
  });

  it("清空全部也真的发请求（否则只是把列表刷一遍）", () => {
    const start = source.indexOf("onClearAll={async (");
    expect(start).toBeGreaterThan(-1);
    expect(source.slice(start, start + 400)).toContain("deleteSessions(personaId)");
  });
});

/**
 * 「清空记忆」是另一个入口（与「清空会话」刻意分开）：
 * 用户会真的要「只清记忆、留着聊天记录」，反之亦然。
 */
describe("控制台清空记忆", () => {
  it("调的是记忆接口，不会顺手把会话也删了", () => {
    const start = source.indexOf("async function confirmPurge()");
    expect(start).toBeGreaterThan(-1);
    const body = source.slice(start, start + 700);
    expect(body).toContain("await purgeMemory(personaId)");
    expect(body).not.toContain("deleteSessions");
    expect(body).not.toContain("deleteSession(");
  });

  it("不可恢复的操作走二次确认（一点就执行是不行的）", () => {
    expect(source).toContain("setPendingPurge(true)");
    expect(source).toContain("确认清空记忆");
  });

  it("文案写清「不可恢复」与「会话不受影响」", () => {
    expect(source).toContain("不可恢复");
    expect(source).toContain("不受影响");
  });
});
