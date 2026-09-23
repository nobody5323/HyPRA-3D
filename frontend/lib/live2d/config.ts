/**
 * Live2D 参数映射与预设的读取层。
 *
 * 两个 JSON（`parameter-map.json` / `presets.json`）是**模型相关的可编辑配置**：
 * 换模型时只改这两份 JSON，渲染器代码不动。
 * 本文件负责把它们读进来、补齐缺失项，并把外部标签归一化到我们自己的情绪 id。
 */

import {
  PORTRAIT_IDS,
  resolvePortraitId,
  type AvatarMotion,
  type PortraitId,
} from "@/lib/avatar/avatar-renderer";
import rawParameterMap from "./parameter-map.json";
import rawPresets from "./presets.json";

/**
 * 参数名映射。
 *
 * 只放**不同模型叫法不同**的参数；Cubism 标准名（ParamMouthOpenY 等）如果模型也用标准名，
 * 保持 JSON 里的默认值即可。
 */
export interface Live2DParameterMap {
  headYaw: string;
  headPitch: string;
  headRoll: string;
  mouthOpen: string;
  mouthForm: string;
  eyeLeftOpen: string;
  eyeRightOpen: string;
  gazeX: string;
  gazeY: string;
  bodyAngle: string;
  breath: string;
}

/** 表情与动作预设 */
export interface Live2DPresets {
  /** 情绪 → 模型表情名；空字符串表示"该情绪不切表情" */
  emotionExpressions: Record<PortraitId, string>;
  /** 具身状态 → 动作组名（模型 motion3.json 里的组名，常见为 Idle） */
  motionGroups: Record<string, string>;
}

/** 动作组兜底名：几乎所有 Cubism 模型都有 Idle */
export const FALLBACK_MOTION_GROUP = "Idle";

const PARAMETER_KEYS: readonly (keyof Live2DParameterMap)[] = [
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
];

function readString(record: Record<string, unknown>, key: string, fallback: string): string {
  const value = record[key];
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

/**
 * 参数映射（缺失项回落 Cubism 标准名）。
 *
 * 回落而不是抛错：模型少几个参数是常态（不是每张图都有呼吸/眼球参数），
 * 渲染器会在设置参数时拿到 `false` 并跳过。
 */
export const LIVE2D_PARAMETER_MAP: Live2DParameterMap = PARAMETER_KEYS.reduce(
  (map, key) => {
    map[key] = readString(rawParameterMap as Record<string, unknown>, key, key);
    return map;
  },
  {} as Live2DParameterMap,
);

/** 预设（缺失项补齐为空串 / Idle，保证结构完整） */
export const LIVE2D_PRESETS: Live2DPresets = (() => {
  const source = rawPresets as {
    emotionExpressions?: Record<string, unknown>;
    motionGroups?: Record<string, unknown>;
  };
  const emotions = (source.emotionExpressions ?? {}) as Record<string, unknown>;
  const motions = (source.motionGroups ?? {}) as Record<string, unknown>;

  const emotionExpressions = {} as Record<PortraitId, string>;
  // 从 PORTRAIT_IDS 派生：新增情绪时自动出现在预设结构里（值为空 = 不切表情）
  for (const key of PORTRAIT_IDS) {
    emotionExpressions[key] = readString(emotions, key, "");
  }

  const motionGroups: Record<string, string> = {};
  for (const key of Object.keys(motions)) {
    motionGroups[key] = readString(motions, key, FALLBACK_MOTION_GROUP);
  }

  return { emotionExpressions, motionGroups };
})();

/**
 * 情绪标签 → 模型表情名。
 *
 * @param presets 预设（默认用 `presets.json`；测试可注入）
 * @returns 表情名；`null` 表示**不要切表情**（预设为空，或情绪未知）。
 *          这里刻意不返回中性表情名：模型不一定有"中性"表情，
 *          硬切一个不存在的名字只会报错。
 */
export function resolveExpressionName(
  label: string | null | undefined,
  presets: Live2DPresets = LIVE2D_PRESETS,
): string | null {
  const id = resolvePortraitId(label);
  return presets.emotionExpressions[id] || null;
}

/** 具身状态 → 动作组名（未知状态回落 Idle，且**不做**动作索引猜测） */
export function resolveMotionGroup(
  motion: AvatarMotion,
  presets: Live2DPresets = LIVE2D_PRESETS,
): string {
  return presets.motionGroups[motion] || FALLBACK_MOTION_GROUP;
}
