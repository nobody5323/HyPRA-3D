import { describe, expect, it } from "vitest";

import {
  EMPTY_DESKTOP_CONTEXT,
  PET_CHANNELS,
  normalizeDesktopContext,
} from "../src/shared/ipc";

/**
 * 桌面情景快照的归一化。
 *
 * 这一层存在的唯一理由是「跨 IPC 后的字段完整性」：采集结果来自 PowerShell 的
 * JSON（外部输出，不可信），字段缺失会让后端的 pydantic 校验直接 422，
 * 而表现是**上报静默失败**——最难查的那一类。
 */
describe("normalizeDesktopContext", () => {
  it("保留完整输入", () => {
    const snapshot = normalizeDesktopContext({
      now_playing_title: "起风了",
      now_playing_artist: "买辣椒也用券",
      foreground_title: "项目周报",
      foreground_process: "Code.exe",
      idle_seconds: 42,
      local_time: "14:30",
      local_date: "2026-09-29",
    });

    expect(snapshot.now_playing_title).toBe("起风了");
    expect(snapshot.foreground_process).toBe("Code.exe");
    expect(snapshot.idle_seconds).toBe(42);
  });

  it("缺失字段回落空值（而不是 undefined）", () => {
    expect(normalizeDesktopContext({})).toEqual(EMPTY_DESKTOP_CONTEXT);
    expect(normalizeDesktopContext(null)).toEqual(EMPTY_DESKTOP_CONTEXT);
    expect(normalizeDesktopContext(undefined)).toEqual(EMPTY_DESKTOP_CONTEXT);
  });

  it("非字符串字段回落空串", () => {
    const snapshot = normalizeDesktopContext({
      now_playing_title: 123,
      foreground_process: { name: "x" },
    });

    expect(snapshot.now_playing_title).toBe("");
    expect(snapshot.foreground_process).toBe("");
  });

  it("去首尾空白", () => {
    expect(normalizeDesktopContext({ foreground_title: "  标题  " }).foreground_title).toBe("标题");
  });

  it("空闲秒数非法时回落 0", () => {
    expect(normalizeDesktopContext({ idle_seconds: "abc" }).idle_seconds).toBe(0);
    expect(normalizeDesktopContext({ idle_seconds: -5 }).idle_seconds).toBe(0);
    expect(normalizeDesktopContext({ idle_seconds: Number.NaN }).idle_seconds).toBe(0);
  });

  it("空闲秒数接受 0（刚动过鼠标是合法状态）", () => {
    expect(normalizeDesktopContext({ idle_seconds: 0 }).idle_seconds).toBe(0);
  });
});

describe("PET_CHANNELS", () => {
  it("桌面情景通道名与 preload 内联的一致", () => {
    // preload 因 sandbox 限制只能内联通道名，写歪了只有运行时才发现——
    // 这条断言把「两边说的是同一个字符串」钉在测试里
    expect(PET_CHANNELS.getDesktopContext).toBe("pet:get-desktop-context");
  });
});
