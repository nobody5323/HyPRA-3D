/**
 * 渲染器适配层契约：把「形象渲染」从对话 / 语音 / 情绪链路里解耦出来。
 *
 * 为什么要单独一层（参照两个开源项目的共同做法：薄适配层 + 资源清单 + 能力自声明）：
 * - 上层只产出**语义**（具身状态 + 情绪标签），不关心形象是图片、Live2D 还是 SDK 渲染；
 * - 渲染器用 `capabilities` 主动声明自己会什么，上层据此决定是否产出更细的数据
 *   （例如不支持 lipSync 的渲染器，就没必要为它生成口型时间轴）；
 * - 换渲染实现时，对话 / 语音 / 记忆链路**零改动**。
 *
 * 实现划分：
 * - `static_portrait`：静态立绘换图 + CSS 动效（当前落地）；
 * - `live2d`：官方 Cubism Web SDK 直连（预留，见 docs/memory-architecture.md 无关，见后续设计）；
 * - 魔珐 SDK **不经过本接口**——它自带完整渲染（TTS + 口型 + 表情 + 动作），
 *   仍走既有 `useAvatar.ts` 的 xmov 链路。
 */

import { AVATAR_STATE_LABELS, type AvatarState, type VisemeFrame } from "./types";

/** 渲染器种类 */
export type AvatarRendererMode = "static_portrait" | "live2d";

/**
 * 动效名 = 具身状态名。
 *
 * 刻意复用 `AvatarState` 而不另立一套词汇：两套状态机并存必然漂移。
 * 动效只驱动 CSS 动画（**不换图**）——说话时用的是「当前表情的那张图」加动效，
 * 避免每说一句就换一张 sprite 造成的闪烁。
 */
export type AvatarMotion = AvatarState;

/** 无动效时的默认值 */
export const DEFAULT_MOTION: AvatarMotion = "idle";

/**
 * 运行时归一化动效名。
 *
 * 渲染循环里不能抛错：非法值一律回落 `idle`，由 `AVATAR_STATE_LABELS` 保证
 * 与 `AvatarState` 全集同步（任一侧新增状态都会在此自动生效）。
 */
export function normalizeMotion(value: unknown): AvatarMotion {
  return typeof value === "string" && hasOwn(AVATAR_STATE_LABELS, value)
    ? (value as AvatarMotion)
    : DEFAULT_MOTION;
}

/**
 * 立绘资源 id：与后端 8 类情绪标签**逐字对应**。
 *
 * 唯一来源是 `backend/app/tools/emotion.py` 的 `EmotionLabel`（happy / calm / sad /
 * anxious / tired / angry / surprised / neutral）。
 * 刻意与后端英文标签同名：少一层翻译就少一处漂移。
 */
export const PORTRAIT_IDS = [
  "happy",
  "calm",
  "sad",
  "anxious",
  "tired",
  "angry",
  "surprised",
  "neutral",
] as const;

/** 立绘 id */
export type PortraitId = (typeof PORTRAIT_IDS)[number];

/** 兜底立绘：必须永远存在，保证任何情况下都有画面 */
export const FALLBACK_PORTRAIT_ID: PortraitId = "neutral";

/**
 * 中文别名（容错用，**不能作为主键**）。
 *
 * 后端 `EMOTION_LABELS_ZH` 里 calm 与 neutral 的中文都是「平静」，中文本身有歧义，
 * 因此调用方应优先传英文 `label`；这里只在拿到中文时才用。
 * 「平静」映射到 neutral 而非 calm：歧义时选视觉上更中性的一侧。
 */
const PORTRAIT_ALIASES: Record<string, PortraitId> = {
  开心: "happy",
  高兴: "happy",
  快乐: "happy",
  愉快: "happy",
  喜悦: "happy",
  平静: "neutral",
  中性: "neutral",
  默认: "neutral",
  冷静: "calm",
  平和: "calm",
  放松: "calm",
  难过: "sad",
  悲伤: "sad",
  伤心: "sad",
  低落: "sad",
  焦虑: "anxious",
  紧张: "anxious",
  不安: "anxious",
  担心: "anxious",
  疲惫: "tired",
  疲倦: "tired",
  累: "tired",
  困: "tired",
  生气: "angry",
  愤怒: "angry",
  恼火: "angry",
  惊讶: "surprised",
  惊喜: "surprised",
  吃惊: "surprised",
};

/**
 * 情绪标签 → 立绘 id。
 *
 * 未知 / 空标签一律回落 `neutral`——**渲染端绝不臆造表情**：
 * 与其显示一张含义不明的图，不如显示中性脸。
 */
export function resolvePortraitId(label: string | null | undefined): PortraitId {
  const key = String(label ?? "").trim().toLowerCase();
  if (!key) return FALLBACK_PORTRAIT_ID;
  if ((PORTRAIT_IDS as readonly string[]).includes(key)) return key as PortraitId;
  // 必须用 hasOwn 后再取值：直接索引会命中原型链（"constructor" → Object 构造函数）
  return hasOwn(PORTRAIT_ALIASES, key) ? PORTRAIT_ALIASES[key] : FALLBACK_PORTRAIT_ID;
}

/** 一张立绘资源（或「表情意图」） */
export interface AvatarExpressionEntry {
  /** 稳定 id（`PortraitId` 或角色包自定义 id）——两个渲染器都按它识别「要什么表情」 */
  id: string;
  /** 展示名（读屏与调试用） */
  name: string;
  /**
   * 图片地址。
   *
   * **静态立绘必需**；Live2D 用不到（它的表情来自模型自带的 exp3.json），
   * 因此声明为可选，避免 Live2D 侧被迫塞一个无意义的空串。
   */
  url?: string;
}

/** 立绘加载失败的上报载荷 */
export interface AvatarImageLoadError {
  expression: AvatarExpressionEntry;
  error: Error;
}

/** 立绘加载失败回调（渲染器不抛错，改为上报） */
export type AvatarImageErrorHandler = (payload: AvatarImageLoadError) => void;

/**
 * 渲染器能力声明。
 *
 * 上层据此降级，**不要靠"猜这个渲染器应该支持"**：例如 `lipSync === false`
 * 时就没有必要为它调用 `/media/avatar` 生成口型时间轴。
 */
export interface AvatarRendererCapabilities {
  /** 能按表情换图 */
  expressionImages: boolean;
  /** 能用 CSS 动效表现 think / speak 等状态 */
  cssMotion: boolean;
  /** 是否 Live2D 渲染 */
  live2d: boolean;
  /** 是否支持口型驱动，为 true 时上层才需要产出时间轴 / 振幅数据 */
  lipSync: boolean;
}

/** 渲染器自述状态（诊断与测试用） */
export interface AvatarRendererStatus {
  mode: AvatarRendererMode;
  characterLabel: string;
  /** 当前生效的立绘（**加载中**的那次切换尚未计入） */
  expression: AvatarExpressionEntry | null;
  motion: AvatarMotion;
  capabilities: AvatarRendererCapabilities;
}

/** 立绘构图校准（随角色包提供，避免为每个角色改 CSS） */
export interface AvatarPortraitLayout {
  /** 缩放（1 = 原始） */
  scale?: number;
  /** 水平位移（px） */
  offsetX?: number;
  /** 垂直位移（px） */
  offsetY?: number;
  /** CSS `transform-origin`，如 "bottom center" */
  anchor?: string;
}

/**
 * 渲染器统一接口。
 *
 * 所有实现都必须满足：
 * 1. **不把错误抛进渲染循环**——加载失败内部消化并通过回调上报；
 * 2. **幂等**——重复设置同一个表情/动效不产生额外副作用（不重新加载、不重启动画）；
 * 3. **`dispose()` 之后不再触碰 DOM**，异步回调也要失效（见各实现的令牌机制）。
 */
export interface AvatarRenderer {
  /** 当前状态快照（每次调用返回新对象，调用方可安全持有） */
  getStatus(): AvatarRendererStatus;
  /** 切换渲染模式（Live2D 预留位；静态实现会在不认识的值上回落） */
  setMode(mode: AvatarRendererMode): AvatarRendererMode;
  /** 设置角色名（读屏与调试标签） */
  setCharacterLabel(label: string): void;
  /** 切换表情；`force` 用于强制重载同一张图（资源更新场景） */
  setExpression(
    entry: AvatarExpressionEntry | null,
    options?: { force?: boolean },
  ): AvatarExpressionEntry | null;
  /** 切换动效；`restart` 用于重新触发同名动效（如连续两次点击） */
  setMotion(motion: AvatarMotion, options?: { restart?: boolean }): AvatarMotion;
  /** 应用构图校准；传空值恢复默认构图 */
  setPortraitLayout(layout?: AvatarPortraitLayout | null): void;
  /**
   * 设置口型时间轴（`null` = 停止并闭嘴）。
   *
   * 只有 `capabilities.lipSync === true` 的渲染器会真正驱动嘴型；
   * 不支持的实现必须是**无副作用的空操作**，调用方无需判断能力。
   */
  setLipSync(timeline: readonly VisemeFrame[] | null): void;
  /** 释放资源（静态实现为空操作；Live2D 需销毁 WebGL 上下文与贴图） */
  dispose(): void;
}

/** 静态立绘渲染器的能力（供各实现复用，避免各处手写不一致） */
export const STATIC_PORTRAIT_CAPABILITIES: AvatarRendererCapabilities = {
  expressionImages: true,
  cssMotion: true,
  live2d: false,
  lipSync: false,
};

/**
 * Live2D 渲染器的能力。
 *
 * 与静态立绘的差别：不依赖图片资源（`expressionImages: false`），表情由模型自带
 * exp3.json 提供；`lipSync: true` —— 用后端 `POST /media/avatar` 的 viseme 时间轴
 * 驱动 `ParamMouthOpenY` / `ParamMouthForm`。
 */
export const LIVE2D_CAPABILITIES: AvatarRendererCapabilities = {
  expressionImages: false,
  cssMotion: false,
  live2d: true,
  lipSync: true,
};

/** 安全的自有属性判断（避免 "constructor" 这类原型链键被误判为合法值） */
function hasOwn(record: object, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}
