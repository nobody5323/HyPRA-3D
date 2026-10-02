/**
 * 叙事框架层取值解析（三端共用）。
 *
 * 这是「界面会不会对用户说谎」的那一行：偏好里的空串含义是「跟随部署默认」，
 * 而部署默认可能是开启的。解析错了，用户就会看到「不使用」而实际每轮都在开
 * ——反过来，把 `"none"`（用户明确关掉）当成空串，部署默认又会把开关点亮。
 */

import { describe, expect, it } from "vitest";

import type { JailbreakCatalog } from "@/lib/api/types";
import { JAILBREAK_NONE, resolveJailbreakId } from "@/lib/chat/jailbreak";

const OFF: JailbreakCatalog = {
  enabled: false,
  default_jailbreak_id: "immersive-narrative",
  presets: [],
};

const ON: JailbreakCatalog = { ...OFF, enabled: true };

describe("resolveJailbreakId", () => {
  it("未设置 + 部署关闭 → 不使用", () => {
    expect(resolveJailbreakId("", OFF)).toBe(JAILBREAK_NONE);
  });

  it("★ 未设置 + 部署开启 → 部署声明的那一档（不能显示成「不使用」）", () => {
    expect(resolveJailbreakId("", ON)).toBe("immersive-narrative");
  });

  it("★ 部署开启但没指定档位 → 不使用（空 id 不能透出去）", () => {
    expect(resolveJailbreakId("", { ...ON, default_jailbreak_id: "" })).toBe(
      JAILBREAK_NONE,
    );
  });

  it("★ 用户明确关掉时，部署默认再开启也不启用", () => {
    expect(resolveJailbreakId(JAILBREAK_NONE, ON)).toBe(JAILBREAK_NONE);
  });

  it("用户选定的档位原样返回（不看部署默认）", () => {
    expect(resolveJailbreakId("mature-fiction", OFF)).toBe("mature-fiction");
  });

  it("清单取不到（离线）时按「不使用」显示", () => {
    expect(resolveJailbreakId("", null)).toBe(JAILBREAK_NONE);
    // 但用户的明确选择仍然照常显示——离线不该抹掉他已经做出的选择
    expect(resolveJailbreakId("mature-fiction", null)).toBe("mature-fiction");
  });
});
