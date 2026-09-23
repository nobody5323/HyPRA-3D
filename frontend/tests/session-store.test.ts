/**
 * 本地会话指针与草稿的持久化测试。
 *
 * 这里刻意**不**测试「消息」的本地存储——消息只有后端一个来源（会话落 SQLite）。
 * 本模块负责的只是「刷新后知道该找哪段会话」以及「没发出去的草稿别丢」。
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  readActiveSessionId,
  readDraft,
  writeActiveSessionId,
  writeDraft,
} from "@/lib/chat/session-store";

beforeEach(() => {
  window.localStorage.clear();
});

describe("会话指针", () => {
  it("按陪伴对象分开记住各自的当前会话", () => {
    writeActiveSessionId("persona-a", "s-a");
    writeActiveSessionId("persona-b", "s-b");

    expect(readActiveSessionId("persona-a")).toBe("s-a");
    expect(readActiveSessionId("persona-b")).toBe("s-b");
    expect(readActiveSessionId("persona-c")).toBeNull(); // 没聊过的角色 = 新对话
  });

  it("写入 null 表示「新建对话」（清除指针）", () => {
    writeActiveSessionId("persona-a", "s-a");
    writeActiveSessionId("persona-a", null);

    expect(readActiveSessionId("persona-a")).toBeNull();
    expect(window.localStorage.getItem("hypra.active-session.persona-a")).toBeNull();
  });

  it("纯空白视为无指针（避免把空白串当会话 id 去请求）", () => {
    window.localStorage.setItem("hypra.active-session.persona-a", "   ");
    expect(readActiveSessionId("persona-a")).toBeNull();
  });
});

describe("输入草稿", () => {
  it("保存与读取（按陪伴对象分开）", () => {
    writeDraft("persona-a", "还没发出去的话");

    expect(readDraft("persona-a")).toBe("还没发出去的话");
    expect(readDraft("persona-b")).toBe("");
  });

  it("空串即清除（刷新后输入框不残留空白草稿）", () => {
    writeDraft("persona-a", "草稿");
    writeDraft("persona-a", "");

    expect(window.localStorage.getItem("hypra.draft.persona-a")).toBeNull();
    expect(readDraft("persona-a")).toBe("");
  });

  it("localStorage 不可用时静默降级（隐私模式不阻断对话）", () => {
    const original = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new Error("localStorage 被禁用");
      },
    });

    expect(() => writeActiveSessionId("persona-a", "s")).not.toThrow();
    expect(readActiveSessionId("persona-a")).toBeNull();
    expect(() => writeDraft("persona-a", "x")).not.toThrow();
    expect(readDraft("persona-a")).toBe("");

    if (original) Object.defineProperty(window, "localStorage", original);
  });
});
