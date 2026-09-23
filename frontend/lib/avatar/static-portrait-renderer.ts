/**
 * 静态立绘渲染器：`<img>` 换图 + CSS 动效。
 *
 * 纯 DOM 实现（不依赖 React），因此同一份逻辑将来也能被桌面端或测试直接复用。
 *
 * 五个来自参考实现（两个成熟桌宠项目）的硬要求，每条都对应一个真实故障模式：
 * 1. **换图有令牌**——预加载完成后若已过期则丢弃，否则「慢的旧图」会覆盖新表情；
 * 2. **不闪白**——新图解码成功前保留旧图可见；
 * 3. **解码尺寸校验**——`naturalWidth === 0`（拿到了字节但解码失败）同样算失败；
 * 4. **动效不换图**——只写 `data-motion`，表现交给 CSS，避免每句台词换 sprite 的闪烁；
 * 5. **不抛错到渲染循环**——失败通过 `onImageLoadError` 上报，调用方决定怎么提示。
 */

import {
  DEFAULT_MOTION,
  STATIC_PORTRAIT_CAPABILITIES,
  normalizeMotion,
  type AvatarExpressionEntry,
  type AvatarImageErrorHandler,
  type AvatarMotion,
  type AvatarPortraitLayout,
  type AvatarRenderer,
  type AvatarRendererMode,
  type AvatarRendererStatus,
} from "./avatar-renderer";

/** 渲染器挂载所需的 DOM 节点 */
export interface StaticPortraitRendererOptions {
  /** 舞台容器：承载 `data-*` 状态，也是 CSS 动效的挂载点 */
  stage: HTMLElement | null;
  /** 立绘图片元素 */
  image: HTMLImageElement | null;
  /** 加载失败回调（可选；缺省时静默，仅更新 data-image-state） */
  onImageLoadError?: AvatarImageErrorHandler;
}

/** 归一化后的构图参数（内部使用，避免每帧重复解析） */
interface NormalizedLayout {
  scale: number;
  offsetX: number;
  offsetY: number;
  anchor: string;
}

export function createStaticPortraitRenderer({
  stage,
  image,
  onImageLoadError,
}: StaticPortraitRendererOptions): AvatarRenderer {
  let disposed = false;
  let characterLabel = "";
  let currentExpression: AvatarExpressionEntry | null = null;
  let currentMotion: AvatarMotion = DEFAULT_MOTION;
  /** 换图令牌：只有最后一次请求的结果允许落地 */
  let expressionLoadToken = 0;
  /** 图片自身 load / error 监听的解绑函数（dispose 时必须调用） */
  let detachImageListeners: (() => void) | null = null;

  bindImageState();
  applyMotion(DEFAULT_MOTION);
  if (stage) stage.dataset.visualRenderer = "static_portrait";

  // ---------------------------------------------------------------
  // 对外接口
  // ---------------------------------------------------------------

  function getStatus(): AvatarRendererStatus {
    return {
      mode: "static_portrait",
      characterLabel,
      expression: currentExpression ? { ...currentExpression } : null,
      motion: currentMotion,
      capabilities: { ...STATIC_PORTRAIT_CAPABILITIES },
    };
  }

  /**
   * 切换渲染模式。
   *
   * 静态实现只可能是 `static_portrait`：请求别的模式时把请求记在 DOM 上
   * （便于诊断「有人要 Live2D 但当前挂的是静态渲染器」），返回值仍是真实模式——
   * **不假装切换成功**。
   */
  function setMode(mode: AvatarRendererMode): AvatarRendererMode {
    if (mode !== "static_portrait" && stage) stage.dataset.rendererRequestedMode = mode;
    return "static_portrait";
  }

  function setCharacterLabel(label: string): void {
    characterLabel = String(label ?? "").trim();
    if (!image || !characterLabel) return;
    image.alt = characterLabel;
    image.title = characterLabel;
    if (stage) stage.setAttribute("aria-label", `${characterLabel}（静态立绘）`);
  }

  /**
   * 切换表情。
   *
   * @param entry 目标立绘；传 `null` 表示**保持不变**（不闪回默认图）
   * @param options.force 强制重新加载同一张图（资源被替换的场景）
   * @returns 本次将要生效的条目（尚未加载完成的也会返回，便于调用方乐观更新）
   */
  function setExpression(
    entry: AvatarExpressionEntry | null,
    options: { force?: boolean } = {},
  ): AvatarExpressionEntry | null {
    const next = normalizeEntry(entry);
    if (!next) return currentExpression;
    if (!options.force && currentExpression?.id === next.id && image?.src) return currentExpression;

    const token = ++expressionLoadToken;

    // 无图片元素（纯状态测试 / 无头宿主）：直接记账
    if (!image) {
      currentExpression = next;
      writeExpressionDataset(next.id);
      return next;
    }

    // 旧图仍在显示时不切到 loading 视觉，避免闪白
    const hasVisibleImage = image.dataset.imageState === "ready" && image.naturalWidth > 0;
    setImageState(hasVisibleImage ? "ready" : "loading");
    if (stage) stage.dataset.expressionPending = next.id;

    void preloadImage(next.url)
      .then(() => {
        // 已过期（更快的新请求已发出 / 已 dispose）：丢弃，不是错误
        if (disposed || token !== expressionLoadToken) return;
        image.src = next.url;
        image.dataset.emotion = next.id;
        currentExpression = next;
        if (characterLabel) image.alt = characterLabel;
        writeExpressionDataset(next.id);
        if (stage) delete stage.dataset.expressionPending;
        setImageState("ready");
      })
      .catch((error: unknown) => {
        if (disposed || token !== expressionLoadToken) return;
        if (stage) delete stage.dataset.expressionPending;
        setImageState(hasVisibleImage ? "ready" : "error");
        onImageLoadError?.({ expression: { ...next }, error: toError(error) });
      });

    return next;
  }

  /** 切换动效（只写 `data-motion`，不换图） */
  function setMotion(motion: AvatarMotion, options: { restart?: boolean } = {}): AvatarMotion {
    const next = normalizeMotion(motion);
    // 重新触发同名动效（例如连续两次点击）：先归零并强制重排，浏览器才会重放动画
    if (options.restart && stage && stage.dataset.motion === next) {
      stage.dataset.motion = DEFAULT_MOTION;
      void stage.offsetWidth;
    }
    applyMotion(next);
    return next;
  }

  /** 应用构图校准（随角色包提供，避免为每个角色改 CSS） */
  function setPortraitLayout(layout?: AvatarPortraitLayout | null): void {
    if (!stage) return;
    const normalized = normalizeLayout(layout);
    if (image) {
      image.style.transform = normalized
        ? `translate(${normalized.offsetX}px, ${normalized.offsetY}px) scale(${normalized.scale})`
        : "";
      image.style.transformOrigin = normalized ? normalized.anchor : "";
    }
    if (normalized) stage.dataset.layoutApplied = "true";
    else delete stage.dataset.layoutApplied;
  }

  /** 释放：让所有挂起的预加载回调失效，解绑监听，并清掉自己写下的瞬时标记 */  function dispose(): void {
    if (disposed) return;
    disposed = true;
    expressionLoadToken += 1; // 使挂起回调全部过期
    detachImageListeners?.();
    detachImageListeners = null;
    /*
     * 清理「本次加载中」的标记。
     *
     * 宿主要可能复用同一个 DOM 节点（React 严格模式的双挂载、或组件重挂载），
     * 残留的 pending 会让新实例误判为「有加载在飞」。
     * 注意只清瞬时标记，不动 `data-expression` / `data-motion` 这类稳态：
     * 那是「当前显示什么」，新实例会自己重新写入。
     */
    if (stage) {
      delete stage.dataset.expressionPending;
      delete stage.dataset.rendererRequestedMode;
    }
  }

  // ---------------------------------------------------------------
  // 内部实现
  // ---------------------------------------------------------------

  /** 监听图片自身的 load / error，把状态同步到 `data-image-state` */
  function bindImageState(): void {
    if (!image) return;
    setImageState(image.getAttribute("src") ? "loading" : "empty");
    const handleLoad = () => setImageState("ready");
    const handleError = () => setImageState("error");
    image.addEventListener("load", handleLoad);
    image.addEventListener("error", handleError);
    detachImageListeners = () => {
      image.removeEventListener("load", handleLoad);
      image.removeEventListener("error", handleError);
    };
  }

  function setImageState(state: "empty" | "loading" | "ready" | "error"): void {
    if (image) image.dataset.imageState = state;
    if (stage) stage.dataset.imageState = state;
  }

  function writeExpressionDataset(id: string): void {
    if (!stage) return;
    stage.dataset.expression = id;
  }

  function applyMotion(motion: AvatarMotion): void {
    currentMotion = motion;
    if (stage) stage.dataset.motion = motion;
  }

  /**
   * 口型：静态立绘**不支持**（`capabilities.lipSync === false`）。
   *
   * 保留为空操作（而不是不实现），这样调用方不必先查能力再调用。
   */
  function setLipSync(): void {
    // 一张静态图无法做口型——显式什么都不做
  }

  return {
    getStatus,
    setMode,
    setCharacterLabel,
    setExpression,
    setMotion,
    setPortraitLayout,
    setLipSync,
    dispose,
  };
}

/** 静态立绘必须有 url：缺 url 视为「无目标」 */
type ResolvedEntry = AvatarExpressionEntry & { url: string };

/** 条目归一化：缺 url 视为「无目标」，调用方应保持现状 */
function normalizeEntry(entry: AvatarExpressionEntry | null): ResolvedEntry | null {
  if (!entry || typeof entry !== "object") return null;
  const url = String(entry.url ?? "").trim();
  if (!url) return null;
  const id = String(entry.id ?? "").trim() || url;
  const name = String(entry.name ?? "").trim() || id;
  return { id, name, url };
}

/** 构图归一化：非法值一律回落安全默认（渲染循环里不抛错） */
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
    // CSS transform-origin 用空格分隔；外部配置常写成 bottom_center
    anchor: anchor ? anchor.replace(/_/g, " ") : "bottom center",
  };
}

/**
 * 预加载并**确认可解码**。
 *
 * 只等 `onload` 不够：拿得到字节不等于解码得出来（尺寸为 0 的图会静默显示空白）。
 */
function preloadImage(url: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const probe = new Image();
    let settled = false;

    const cleanup = () => {
      probe.onload = null;
      probe.onerror = null;
    };
    const fail = (error: unknown) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(toError(error));
    };
    const succeed = () => {
      if (settled) return;
      if (probe.naturalWidth > 0 && probe.naturalHeight > 0) {
        settled = true;
        cleanup();
        resolve();
        return;
      }
      fail(new Error("image_decoded_with_empty_size"));
    };
    const decode = () => {
      // decode() 能在图片「可绘制」后才 resolve；不支持时退回 onload 时机
      if (typeof probe.decode === "function") {
        probe.decode().then(succeed).catch(fail);
        return;
      }
      succeed();
    };

    probe.decoding = "async";
    probe.onload = decode;
    probe.onerror = () => fail(new Error("image_load_failed"));
    probe.src = url;
    // 命中缓存时 onload 可能已经错过，需要补一次
    if (probe.complete) queueMicrotask(decode);
  });
}

function toError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value));
}
