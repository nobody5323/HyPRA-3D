/**
 * Live2D 配置层测试：参数映射与预设的读取、归一化。
 *
 * 重点在「缺配置时的行为」：模型少参数、预设留空都是常态，
 * 这些情况下必须**安静地不做**，而不是切一个不存在的表情/参数名。
 */

import { describe, expect, it } from "vitest";

import { PORTRAIT_IDS } from "@/lib/avatar-renderer";
import {
  FALLBACK_MOTION_GROUP,
  LIVE2D_PARAMETER_MAP,
  LIVE2D_PRESETS,
  resolveExpressionName,
  resolveMotionGroup,
  type Live2DPresets,
} from "@/lib/live2d/config";

const PARAMETER_KEYS = [
  "headYaw",
  "headPitch",
  "headRoll",
  "mouthOpen",
  "mouthForm",
  "eyeLeftOpen",
  "eyeRightOpen",
  "gazeX",
  "gazeY",
  "bodyAngle",
  "breath",
] as const;

/** 全部留空：仓库默认状态（还没有为任何情绪配表情） */
const EMPTY_PRESETS: Live2DPresets = {
  emotionExpressions: {
    happy: "",
    calm: "",
    sad: "",
    anxious: "",
    tired: "",
    angry: "",
    surprised: "",
    neutral: "",
  },
  motionGroups: { idle: "Idle" },
};

/** 注入用预设：只给 happy 配了表情名，其余留空 */
const PARTIAL_PRESETS: Live2DPresets = {
  emotionExpressions: {
    ...EMPTY_PRESETS.emotionExpressions,
    happy: "开心",
  },
  motionGroups: { idle: "Idle", think: "Think" },
};

describe("LIVE2D_PARAMETER_MAP", () => {
  it("11 项参数名都已填充，且全部以 Param 开头（Cubism 约定）", () => {
    for (const key of PARAMETER_KEYS) {
      const value = LIVE2D_PARAMETER_MAP[key];
      expect(value, `缺少 ${key}`).toBeTruthy();
      expect(value.startsWith("Param"), `${key} = ${value}`).toBe(true);
    }
  });
});

describe("LIVE2D_PRESETS", () => {
  it("补齐 8 类情绪，结构完整（仓库默认全部留空 = 不切表情）", () => {
    expect(Object.keys(LIVE2D_PRESETS.emotionExpressions).sort()).toEqual(
      [...PORTRAIT_IDS].sort(),
    );
    for (const id of PORTRAIT_IDS) {
      expect(typeof LIVE2D_PRESETS.emotionExpressions[id]).toBe("string");
    }
  });
});

describe("resolveExpressionName", () => {
  it("预设留空时返回 null（不切表情，而不是猜一个名字）", () => {
    expect(resolveExpressionName("happy", EMPTY_PRESETS)).toBeNull();
    expect(resolveExpressionName("sad", EMPTY_PRESETS)).toBeNull();
  });

  it("预设填写后按情绪 id 命中", () => {
    expect(resolveExpressionName("happy", PARTIAL_PRESETS)).toBe("开心");
  });

  it("中文标签也能命中（复用情绪归一化）", () => {
    expect(resolveExpressionName("开心", PARTIAL_PRESETS)).toBe("开心");
  });

  it("未知情绪与空值都落到 neutral 的预设（默认仍为空）", () => {
    expect(resolveExpressionName("ecstatic", EMPTY_PRESETS)).toBeNull();
    expect(resolveExpressionName(null, EMPTY_PRESETS)).toBeNull();
    expect(resolveExpressionName(undefined, EMPTY_PRESETS)).toBeNull();
  });
});

describe("resolveMotionGroup", () => {
  it("按预设取动作组", () => {
    expect(resolveMotionGroup("idle", PARTIAL_PRESETS)).toBe("Idle");
    expect(resolveMotionGroup("think", PARTIAL_PRESETS)).toBe("Think");
  });

  it("预设缺少该状态时回落 Idle（不做动作索引猜测）", () => {
    expect(resolveMotionGroup("speak", PARTIAL_PRESETS)).toBe(FALLBACK_MOTION_GROUP);
  });
});
