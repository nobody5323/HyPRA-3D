/**
 * Live2D 渲染器：实现 `AvatarRenderer` 接口（capabilities.live2d = true）。
 *
 * 它**不直接依赖官方 SDK**，只依赖 `CubismBridge` 这一层薄接口，因此：
 * - 未安装 SDK 时由 alias 解析到 unavailable 实现 → 立刻降级，不进入加载；
 * - 全部逻辑（表情映射、动作切换、就绪前意图缓存、帧循环、释放）都能用
 *   **假桥**（fake bridge）单测，不需要 SDK / 模型 / WebGL。
 *
 * 与静态立绘渲染器的行为差异（都在注释里写明，避免上层误用）：
 * - `setExpression` 只使用 `entry.id`（情绪 id），`url` 无意义；
 * - 预设里没有对应表情名时**不切表情**（宁可不动，也不猜一个不存在的表情）；
 * - 动效交给模型自带的 motion3.json，不再用 CSS 动画。
 */

import {
  DEFAULT_MOTION,
  LIVE2D_CAPABILITIES,
  normalizeMotion,
  type AvatarExpressionEntry,
  type AvatarMotion,
  type AvatarPortraitLayout,
  type AvatarRenderer,
  type AvatarRendererMode,
  type AvatarRendererStatus,
} from "../avatar-renderer";
import {
  CUBISM_SHADER_BASE_URL,
  type CubismBridge,
  type CubismModelHandle,
} from "./cubism-bridge";
import { loadCubismCore } from "./cubism-core";
import {
  LIVE2D_PARAMETER_MAP,
  LIVE2D_PRESETS,
  resolveExpressionName,
  resolveMotionGroup,
  type Live2DParameterMap,
  type Live2DPresets,
} from "./config";
import { sampleMouth } from "../lipsync";
import type { Live2DModelEntry } from "./model-assets";
import type { VisemeFrame } from "../types";

/** 模型加载阶段（比 `AvatarRendererStatus` 更细，供诊断面板展示） */
export type Live2DLoadStage = "idle" | "loading" | "ready" | "failed";

/** 渲染器扩展接口：多出加载阶段与参数诊断 */
export interface Live2DRenderer extends AvatarRenderer {
  readonly loadStage: Live2DLoadStage;
  readonly detail: string;
  /** 模型实际拥有的参数名（用于核对 parameter-map.json 是否匹配该模型） */
  parameterNames(): readonly string[];
}

export interface Live2DRendererOptions {
  /** 舞台容器（承载 `data-*` 状态） */
  stage: HTMLElement | null;
  /** 渲染画布（WebGL 目标） */
  canvas: HTMLCanvasElement | null;
  /**
   * Cubism 运行时桥的**加载器**（不是桥本身）。
   *
   * 为什么必须是加载器：官方 Framework 在**模块求值阶段**就会读全局
   * `Live2DCubismCore`，所以它只能在 Core 注入完成之后才能被 `import`。
   * 也就是说 `import bridge from "@cubism-bridge"` **不能出现在任何模块顶层**。
   */
  loadBridge: () => Promise<CubismBridge>;
  /** 模型条目 */
  model: Live2DModelEntry;
  /** 参数映射（默认读 `parameter-map.json`） */
  parameterMap?: Live2DParameterMap;
  /** 表情/动作预设（默认读 `presets.json`） */
  presets?: Live2DPresets;
  /** shader 目录（官方 SDK 运行时 fetch） */
  shaderBaseUrl?: string;
  /** 加载或运行期错误上报（渲染器自身不抛错） */
  onError?: (error: Error) => void;
  /** 阶段变化上报（诊断展示） */
  onStageChange?: (stage: Live2DLoadStage, detail: string) => void;
  /**
   * Cubism Core 加载器。
   *
   * Core 是 UMD，必须先用 `<script>` 注入（官方 Framework 依赖全局
   * `Live2DCubismCore`），所以它在 `bridge.init()` **之前**执行。
   * 默认用真实实现；测试可注入。
   */
  loadCore?: () => Promise<void>;
  /** 帧循环注入（测试用；默认 requestAnimationFrame） */
  requestFrame?: (callback: (time: number) => void) => number;
  /** 帧循环取消注入（测试用） */
  cancelFrame?: (handle: number) => void;
  /** 时钟注入（测试用） */
  now?: () => number;
}

/** 单帧最大推进时长（秒）：防止标签页恢复时的一次性大跳 */
const MAX_FRAME_DELTA_SECONDS = 0.1;

export function createLive2DRenderer(options: Live2DRendererOptions): Live2DRenderer {
  const { stage, canvas } = options;
  const parameterMap = options.parameterMap ?? LIVE2D_PARAMETER_MAP;
  const presets = options.presets ?? LIVE2D_PRESETS;
  const shaderBaseUrl = options.shaderBaseUrl ?? CUBISM_SHADER_BASE_URL;
  const requestFrame =
    options.requestFrame ?? ((callback: (time: number) => void) => requestAnimationFrame(callback));
  const cancelFrame = options.cancelFrame ?? ((handle: number) => cancelAnimationFrame(handle));
  const now = options.now ?? (() => performance.now());
  const loadCore = options.loadCore ?? loadCubismCore;
  const loadBridge = options.loadBridge;

  let disposed = false;
  let loadToken = 0;
  let handle: CubismModelHandle | null = null;
  let loadStage: Live2DLoadStage = "idle";
  let detail = "";
  let characterLabel = "";
  let currentExpression: AvatarExpressionEntry | null = null;
  let currentMotion: AvatarMotion = DEFAULT_MOTION;
  let frameHandle: number | null = null;
  let lastFrameTime = 0;
  /** 模型就绪前收到的表情意图（就绪后补播，避免首屏情绪丢失） */
  let pendingExpressionId: string | null = null;
  /** 当前是否挂着一张表情（决定收回时要不要真的 reset） */
  let expressionApplied = false;
  /** 口型时间轴与其起始时刻（`null` = 不说话） */
  let lipSyncTimeline: readonly VisemeFrame[] | null = null;
  let lipSyncStartMs = 0;

  if (stage) {
    stage.dataset.visualRenderer = "live2d";
    stage.dataset.motion = DEFAULT_MOTION;
  }

  void startLoad();

  // ---------------------------------------------------------------
  // 加载与帧循环
  // ---------------------------------------------------------------

  async function startLoad(): Promise<void> {
    if (disposed) return;
    if (!canvas) {
      setStage("failed", "缺少用于 WebGL 渲染的画布元素");
      return;
    }

    const token = ++loadToken;
    setStage("loading", "正在加载 Cubism Core…");
    try {
      // ① Core 必须先于任何 SDK 调用：Framework 模块求值时就会读全局对象
      await loadCore();
      if (disposed || token !== loadToken) return;

      // ② 此时才能安全地 import 官方 Framework（经由运行时桥）
      setStage("loading", "正在初始化 Cubism 运行时…");
      const bridge = await loadBridge();
      if (disposed || token !== loadToken) return;
      if (!bridge.available) {
        setStage("failed", bridge.unavailableReason);
        return;
      }

      setStage("loading", `正在加载模型：${options.model.modelUrl}`);
      bridge.init();
      const loaded = await bridge.load({
        canvas,
        modelUrl: options.model.modelUrl,
        shaderBaseUrl,
        onLog: (message) => {
          detail = message;
        },
      });
      // 过期（已 dispose 或已有更新的加载）：立刻释放，绝不让 WebGL 资源泄漏
      if (disposed || token !== loadToken) {
        loaded.release();
        return;
      }
      handle = loaded;
      applyLayout(options.model.layout);
      setStage(
        "ready",
        `模型已就绪：${handle.parameterNames().length} 个参数，${describeMissingParameters()}`,
      );
      startFrameLoop();
      // 补播加载期间到达的表情意图
      if (pendingExpressionId) {
        const id = pendingExpressionId;
        pendingExpressionId = null;
        playExpressionFor(id);
      }
    } catch (error) {
      if (disposed || token !== loadToken) return;
      const failure = toError(error);
      setStage("failed", failure.message);
      options.onError?.(failure);
    }
  }

  function startFrameLoop(): void {
    if (frameHandle !== null || disposed) return;
    lastFrameTime = now();
    const tick = (time: number): void => {
      if (disposed || !handle) return;
      // 帧间隔上限：标签页被挂起后回来时 rAF 会给出一个巨大的时间差，
      // 直接喂给动作/物理会让模型瞬移。这是渲染器的职责（桥只做 SDK 调用）。
      const delta = Math.min(Math.max(0, (time - lastFrameTime) / 1000), MAX_FRAME_DELTA_SECONDS);
      lastFrameTime = time;
      try {
        // 口型必须在 update 之前写入：SDK 在 update 里提交参数并绘制，
        // update 之后再设只会晚一帧。
        applyLipSync(time);
        handle.update(delta);
      } catch (error) {
        // 绘制出错就停下并上报：继续跑只会每帧刷屏
        stopFrameLoop();
        const failure = toError(error);
        setStage("failed", `渲染中断：${describeFailure(failure)}`);
        options.onError?.(failure);
        return;
      }
      frameHandle = requestFrame(tick);
    };
    frameHandle = requestFrame(tick);
  }

  function stopFrameLoop(): void {
    if (frameHandle === null) return;
    cancelFrame(frameHandle);
    frameHandle = null;
  }

  // ---------------------------------------------------------------
  // 表情 / 动作 / 构图
  // ---------------------------------------------------------------

  function setExpression(
    entry: AvatarExpressionEntry | null,
    config: { force?: boolean } = {},
  ): AvatarExpressionEntry | null {
    const next = normalizeIntent(entry);
    if (!next) return currentExpression;
    if (!config.force && currentExpression?.id === next.id) return currentExpression;

    currentExpression = next;
    if (stage) stage.dataset.expression = next.id;

    if (!handle) {
      // 模型还没就绪：记住意图，就绪后补播（首屏情绪不能丢）
      pendingExpressionId = next.id;
      return next;
    }
    playExpressionFor(next.id);
    return next;
  }

  function playExpressionFor(emotionId: string): void {
    if (!handle) return;
    const expressionName = resolveExpressionName(emotionId, presets);

    if (!expressionName) {
      // 预设为空 = 该情绪没有对应表情，应当展示**中性脸**。
      // 但不能只是"什么都不做"：表情是叠加的，上一张脸会残留下来。
      if (expressionApplied) {
        handle.resetExpression();
        expressionApplied = false;
      }
      return;
    }

    if (handle.playExpression(expressionName)) {
      expressionApplied = true;
      return;
    }
    // 模型没有这个表情：不臆造、不抛错，只记一次诊断
    detail = `模型没有名为「${expressionName}」的表情，请核对 presets.json`;
  }

  function setMotion(motion: AvatarMotion, config: { restart?: boolean } = {}): AvatarMotion {
    const next = normalizeMotion(motion);
    if (config.restart && stage && stage.dataset.motion === next) {
      stage.dataset.motion = DEFAULT_MOTION;
      void stage.offsetWidth;
    }
    currentMotion = next;
    if (stage) stage.dataset.motion = next;

    // 动作组交给模型自带的 motion3.json；回到 idle 时不打断（桥内部维持 Idle 循环）
    if (handle && next !== DEFAULT_MOTION) {
      const group = resolveMotionGroup(next, presets);
      const configured = Boolean(presets.motionGroups[next]);
      if (!handle.playMotion(group, 0, true)) {
        detail = `模型没有动作组「${group}」，请核对 presets.json 的 motionGroups`;
      } else if (!configured) {
        // 静默回落会让用户以为"动作坏了"：这里说明是配置没覆盖
        detail = `presets.json 未配置「${next}」的动作组，已回落到 ${group}`;
      }
    }
    return next;
  }

  function setPortraitLayout(layout?: AvatarPortraitLayout | null): void {
    applyLayout(layout);
  }

  function applyLayout(layout?: AvatarPortraitLayout | null): void {
    if (stage) {
      if (layout) stage.dataset.layoutApplied = "true";
      else delete stage.dataset.layoutApplied;
    }
    if (!canvas) return;
    const normalized = normalizeLayout(layout);
    if (!normalized) {
      canvas.style.transform = "";
      canvas.style.transformOrigin = "";
      handle?.resize();
      return;
    }
    canvas.style.transform = `translate(${normalized.offsetX}px, ${normalized.offsetY}px) scale(${normalized.scale})`;
    canvas.style.transformOrigin = normalized.anchor;
    // 缩放会改变可视区域，需要按新尺寸重算投影
    handle?.resize();
  }

  // ---------------------------------------------------------------
  // 状态与释放
  // ---------------------------------------------------------------

  function setStage(next: Live2DLoadStage, nextDetail: string): void {
    loadStage = next;
    detail = nextDetail;
    if (stage) stage.dataset.live2dStage = next;
    options.onStageChange?.(next, nextDetail);
  }

  function getStatus(): AvatarRendererStatus {
    return {
      mode: "live2d",
      characterLabel,
      expression: currentExpression ? { ...currentExpression } : null,
      motion: currentMotion,
      capabilities: { ...LIVE2D_CAPABILITIES },
    };
  }

  /**
   * 切换渲染模式。
   *
   * 只可能是 `live2d`；请求别的模式时把请求记在 DOM 上（诊断用），
   * 返回值仍是真实模式——不假装切换成功。
   */
  function setMode(mode: AvatarRendererMode): AvatarRendererMode {
    if (mode !== "live2d" && stage) stage.dataset.rendererRequestedMode = mode;
    return "live2d";
  }

  function setCharacterLabel(label: string): void {
    characterLabel = String(label ?? "").trim();
    if (!stage || !characterLabel) return;
    stage.setAttribute("aria-label", `${characterLabel}（Live2D 模型）`);
  }

  function parameterNames(): readonly string[] {
    return handle?.parameterNames() ?? [];
  }

  /**
   * 设置口型时间轴（`null` = 停止并闭嘴）。
   *
   * 起始时刻在**收到时间轴的这一刻**取：后端时间轴是相对播报开始的相对时间，
   * 而播报（浏览器 TTS）与取时间轴几乎同时启动，因此用本地时钟对齐即可。
   */
  function setLipSync(timeline: readonly VisemeFrame[] | null): void {
    lipSyncTimeline = timeline && timeline.length > 0 ? timeline : null;
    lipSyncStartMs = now();
    if (lipSyncTimeline) return;
    // 停止口型时立刻闭嘴，而不是等下一帧——否则嘴会停在最后一次张开的样子
    handle?.setParameter(parameterMap.mouthOpen, 0);
    handle?.setParameter(parameterMap.mouthForm, 0);
  }

  /** 把时间轴采样成嘴部参数（每个渲染帧调一次） */
  function applyLipSync(time: number): void {
    if (!handle || !lipSyncTimeline) return;
    const shape = sampleMouth(lipSyncTimeline, time - lipSyncStartMs);
    handle.setParameter(parameterMap.mouthOpen, shape.openness);
    handle.setParameter(parameterMap.mouthForm, shape.form);
  }

  /** 参数映射与模型参数集的差异（诊断用，不阻断渲染：模型少几个参数是常态） */
  function describeMissingParameters(): string {
    const available = new Set(handle?.parameterNames() ?? []);
    const mapped = Object.values(parameterMap);
    const missing = mapped.filter((name) => !available.has(name));
    if (missing.length === 0) return "参数映射全部命中";
    return `parameter-map 有 ${missing.length} 项未命中（${missing.slice(0, 3).join("、")}…）`;
  }

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    loadToken += 1; // 让挂起的加载回调失效（回调里会自行 release）
    stopFrameLoop();
    handle?.release();
    handle = null;
    if (stage) delete stage.dataset.live2dStage;
  }

  return {
    get loadStage() {
      return loadStage;
    },
    get detail() {
      return detail;
    },
    getStatus,
    setMode,
    setCharacterLabel,
    setExpression,
    setMotion,
    setPortraitLayout,
    setLipSync,
    parameterNames,
    dispose,
  };
}

/** 表情意图归一化：只保留 id / name（Live2D 不消费 url） */
function normalizeIntent(entry: AvatarExpressionEntry | null): AvatarExpressionEntry | null {
  if (!entry || typeof entry !== "object") return null;
  const id = String(entry.id ?? "").trim();
  if (!id) return null;
  return { id, name: String(entry.name ?? "").trim() || id };
}

interface NormalizedLayout {
  scale: number;
  offsetX: number;
  offsetY: number;
  anchor: string;
}

function normalizeLayout(layout?: AvatarPortraitLayout | null): NormalizedLayout | null {
  if (!layout || typeof layout !== "object") return null;
  const scale = Number(layout.scale);
  const offsetX = Number(layout.offsetX);
  const offsetY = Number(layout.offsetY);
  const anchor = String(layout.anchor ?? "").trim();
  return {
    scale: Number.isFinite(scale) && scale > 0 ? Math.min(5, Math.max(0.1, scale)) : 1,
    offsetX: Number.isFinite(offsetX) ? offsetX : 0,
    offsetY: Number.isFinite(offsetY) ? offsetY : 0,
    anchor: anchor ? anchor.replace(/_/g, " ") : "bottom center",
  };
}

function toError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value));
}

/**
 * 给错误补一个位置。
 *
 * 「渲染中断」类错误几乎都抛在官方 SDK 深处（如 cubismmotion.ts:408），
 * 只报 message 时定位成本极高——这里带上堆栈里第一条非 node_modules 的帧。
 */
function describeFailure(error: Error): string {
  const frame = error.stack
    ?.split("\n")
    .slice(1)
    .map((line) => line.trim().replace(/^at\s+/, ""))
    .find((line) => line.includes("/") && !line.includes("node_modules"));
  return frame ? `${error.message}（${frame}）` : error.message;
}
