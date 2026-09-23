/**
 * 渲染器适配层契约的纯函数测试。
 *
 * 这一层不碰 DOM，重点锁三件事：
 * 1. **与后端情绪标签的一致性**——`PORTRAIT_IDS` 必须逐字等于
 *    `backend/app/tools/emotion.py` 的 `EmotionLabel`（后端新增情绪时此处会失败，
 *    提醒同步立绘资源，而不是静默显示错的表情）；
 * 2. **不臆造表情**——未知 / 空 / 原型链键一律回落 neutral；
 * 3. **不抛错到渲染循环**——非法动效名回落 idle。
 */

import { describe, expect, it } from "vitest";

import {
  DEFAULT_MOTION,
  FALLBACK_PORTRAIT_ID,
  PORTRAIT_IDS,
  STATIC_PORTRAIT_CAPABILITIES,
  normalizeMotion,
  resolvePortraitId,
} from "@/lib/avatar/avatar-renderer";
import { AVATAR_STATE_LABELS } from "@/lib/api/types";

/** 后端 EmotionLabel 的 8 类（逐字复制，作为一致性基准） */
const BACKEND_EMOTION_LABELS = [
  "happy",
  "calm",
  "sad",
  "anxious",
  "tired",
  "angry",
  "surprised",
  "neutral",
];

describe("PORTRAIT_IDS 与后端情绪标签", () => {
  it("数量与取值都与 backend/app/tools/emotion.py 的 EmotionLabel 一致", () => {
    expect([...PORTRAIT_IDS].sort()).toEqual([...BACKEND_EMOTION_LABELS].sort());
    expect(PORTRAIT_IDS).toHaveLength(8);
  });

  it("兜底立绘在合法 id 集合内", () => {
    expect(PORTRAIT_IDS).toContain(FALLBACK_PORTRAIT_ID);
  });
});

describe("resolvePortraitId", () => {
  it("8 个英文标签各自映射到自身", () => {
    for (const label of BACKEND_EMOTION_LABELS) {
      expect(resolvePortraitId(label)).toBe(label);
    }
  });

  it("容忍大小写与首尾空白", () => {
    expect(resolvePortraitId("  Happy ")).toBe("happy");
    expect(resolvePortraitId("ANXIOUS")).toBe("anxious");
  });

  it("支持中文别名容错（label 缺失时的退路）", () => {
    expect(resolvePortraitId("开心")).toBe("happy");
    expect(resolvePortraitId("难过")).toBe("sad");
    expect(resolvePortraitId("焦虑")).toBe("anxious");
    expect(resolvePortraitId("疲惫")).toBe("tired");
    expect(resolvePortraitId("生气")).toBe("angry");
    expect(resolvePortraitId("惊讶")).toBe("surprised");
    expect(resolvePortraitId("冷静")).toBe("calm");
  });

  it("中文「平静」在 calm / neutral 上有歧义时落到 neutral（不臆造）", () => {
    // 后端 EMOTION_LABELS_ZH 里 calm 与 neutral 的中文都是「平静」，
    // 无法区分；歧义时选视觉上更中性的一侧。
    expect(resolvePortraitId("平静")).toBe("neutral");
  });

  it("未知标签回落 neutral，而不是猜一个表情", () => {
    expect(resolvePortraitId("ecstatic")).toBe(FALLBACK_PORTRAIT_ID);
    expect(resolvePortraitId("")).toBe(FALLBACK_PORTRAIT_ID);
    expect(resolvePortraitId(null)).toBe(FALLBACK_PORTRAIT_ID);
    expect(resolvePortraitId(undefined)).toBe(FALLBACK_PORTRAIT_ID);
  });

  it("原型链上的键不会被误判为合法表情", () => {
    expect(resolvePortraitId("constructor")).toBe(FALLBACK_PORTRAIT_ID);
    expect(resolvePortraitId("__proto__")).toBe(FALLBACK_PORTRAIT_ID);
    expect(resolvePortraitId("toString")).toBe(FALLBACK_PORTRAIT_ID);
  });
});

describe("normalizeMotion", () => {
  it("4 个具身状态原样返回", () => {
    expect(normalizeMotion("idle")).toBe("idle");
    expect(normalizeMotion("listen")).toBe("listen");
    expect(normalizeMotion("think")).toBe("think");
    expect(normalizeMotion("speak")).toBe("speak");
  });

  it("非法值回落 idle（不把错误抛进渲染循环）", () => {
    expect(normalizeMotion("IDLE")).toBe(DEFAULT_MOTION);
    expect(normalizeMotion("dancing")).toBe(DEFAULT_MOTION);
    expect(normalizeMotion("")).toBe(DEFAULT_MOTION);
    expect(normalizeMotion(null)).toBe(DEFAULT_MOTION);
    expect(normalizeMotion(undefined)).toBe(DEFAULT_MOTION);
    expect(normalizeMotion(3)).toBe(DEFAULT_MOTION);
    expect(normalizeMotion({})).toBe(DEFAULT_MOTION);
  });

  it("原型链上的键不会被误判为合法动效", () => {
    expect(normalizeMotion("constructor")).toBe(DEFAULT_MOTION);
  });

  it("覆盖 AvatarState 的全部取值（两套词汇不会漂移）", () => {
    const states = Object.keys(AVATAR_STATE_LABELS);
    expect([...states].sort()).toEqual(["idle", "listen", "speak", "think"].sort());
    for (const state of states) {
      expect(normalizeMotion(state)).toBe(state);
    }
  });
});

describe("静态立绘渲染器的能力声明", () => {
  it("声明支持换图与 CSS 动效，但明确不支持 Live2D 与口型", () => {
    expect(STATIC_PORTRAIT_CAPABILITIES.expressionImages).toBe(true);
    expect(STATIC_PORTRAIT_CAPABILITIES.cssMotion).toBe(true);
    expect(STATIC_PORTRAIT_CAPABILITIES.live2d).toBe(false);
    expect(STATIC_PORTRAIT_CAPABILITIES.lipSync).toBe(false);
  });
});
