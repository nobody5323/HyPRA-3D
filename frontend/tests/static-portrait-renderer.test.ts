/**
 * 静态立绘渲染器测试（纯 DOM，jsdom）。
 *
 * 这里锁的都是**真实故障模式**，不是覆盖率：
 * - 换图竞态：慢的旧请求回来后不能覆盖新表情（否则用户看到的表情与情绪对不上）；
 * - 解码失败：拿到字节但尺寸为 0 必须算失败，否则显示空白却报"成功"；
 * - 失败不抛错：渲染循环里抛错会打断整条对话链路；
 * - dispose 之后：挂起的回调不许再碰 DOM（组件卸载后的经典泄漏）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PORTRAIT_MANIFEST } from "@/lib/avatar/portrait-assets";
import { createStaticPortraitRenderer } from "@/lib/avatar/static-portrait-renderer";

/**
 * 可控的 `Image` 替身。
 *
 * 构造时不自动完成加载——由测试主动调用 `resolveLoad()` / `rejectLoad()`，
 * 这样才能精确复现「两个请求乱序返回」这类时序。
 */
class FakeImage {
  static instances: FakeImage[] = [];

  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  decoding = "";
  complete = false;
  naturalWidth = 0;
  naturalHeight = 0;

  private currentSrc = "";

  get src(): string {
    return this.currentSrc;
  }

  set src(value: string) {
    this.currentSrc = value;
    FakeImage.instances.push(this);
  }

  /** `decode()` 默认可立即解析；失败路径由 `resolveLoad(0, 0)` 覆盖 */
  decode(): Promise<void> {
    return Promise.resolve();
  }

  /** 测试辅助：模拟加载成功（尺寸为 0 表示"解码不出内容"） */
  resolveLoad(width = 320, height = 420): void {
    this.naturalWidth = width;
    this.naturalHeight = height;
    this.onload?.();
  }

  /** 测试辅助：模拟网络/解析失败 */
  rejectLoad(): void {
    this.onerror?.();
  }
}

/** 冲掉所有 microtask（预加载 = Promise 链 + queueMicrotask） */
function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function setup() {
  const stage = document.createElement("div");
  const image = document.createElement("img");
  image.setAttribute("src", PORTRAIT_MANIFEST.neutral.url);
  document.body.append(stage, image);
  const onImageLoadError = vi.fn();
  const renderer = createStaticPortraitRenderer({ stage, image, onImageLoadError });
  return { stage, image, renderer, onImageLoadError };
}

beforeEach(() => {
  FakeImage.instances = [];
  vi.stubGlobal("Image", FakeImage);
});

afterEach(() => {
  document.body.innerHTML = "";
});

describe("createStaticPortraitRenderer", () => {
  it("初始状态自述为静态立绘渲染器，并声明能力边界", () => {
    const { stage, renderer } = setup();
    const status = renderer.getStatus();
    expect(status.mode).toBe("static_portrait");
    expect(status.motion).toBe("idle");
    expect(status.expression).toBeNull();
    expect(status.capabilities).toEqual({
      expressionImages: true,
      cssMotion: true,
      live2d: false,
      lipSync: false,
    });
    expect(stage.dataset.visualRenderer).toBe("static_portrait");
    expect(stage.dataset.motion).toBe("idle");
  });

  it("请求 Live2D 模式时不假装成功，只把请求记在 DOM 上", () => {
    const { stage, renderer } = setup();
    expect(renderer.setMode("live2d")).toBe("static_portrait");
    expect(stage.dataset.rendererRequestedMode).toBe("live2d");
  });

  it("换图：预加载成功后才改 src，并清掉 pending 标记", async () => {
    const { stage, image, renderer } = setup();
    renderer.setExpression(PORTRAIT_MANIFEST.happy);

    // 预加载尚未完成：src 还是旧的 neutral，pending 已标记
    expect(image.getAttribute("src")).toBe(PORTRAIT_MANIFEST.neutral.url);
    expect(stage.dataset.expressionPending).toBe("happy");

    FakeImage.instances.at(-1)?.resolveLoad();
    await flush();

    expect(image.getAttribute("src")).toBe(PORTRAIT_MANIFEST.happy.url);
    expect(stage.dataset.expression).toBe("happy");
    expect(stage.dataset.imageState).toBe("ready");
    expect(stage.dataset.expressionPending).toBeUndefined();
    expect(renderer.getStatus().expression).toEqual(PORTRAIT_MANIFEST.happy);
  });

  it("同一表情重复设置时不重复请求图片（幂等）", async () => {
    const { renderer } = setup();
    renderer.setExpression(PORTRAIT_MANIFEST.calm);
    FakeImage.instances.at(-1)?.resolveLoad();
    await flush();
    const requestsAfterFirst = FakeImage.instances.length;

    renderer.setExpression(PORTRAIT_MANIFEST.calm);
    await flush();

    expect(FakeImage.instances.length).toBe(requestsAfterFirst);
  });

  it("force 时强制重新加载同一张图（资源被替换的场景）", async () => {
    const { renderer } = setup();
    renderer.setExpression(PORTRAIT_MANIFEST.calm);
    FakeImage.instances.at(-1)?.resolveLoad();
    await flush();
    const before = FakeImage.instances.length;

    renderer.setExpression(PORTRAIT_MANIFEST.calm, { force: true });
    await flush();

    expect(FakeImage.instances.length).toBe(before + 1);
  });

  it("竞态：慢的旧请求回来后不许覆盖新表情", async () => {
    const { stage, image, renderer } = setup();

    renderer.setExpression(PORTRAIT_MANIFEST.sad);
    const slow = FakeImage.instances.at(-1);

    renderer.setExpression(PORTRAIT_MANIFEST.angry);
    const fast = FakeImage.instances.at(-1);
    expect(fast).not.toBe(slow);

    // 新请求先完成
    fast?.resolveLoad();
    await flush();
    expect(image.getAttribute("src")).toBe(PORTRAIT_MANIFEST.angry.url);

    // 旧请求随后才完成：必须被丢弃
    slow?.resolveLoad();
    await flush();
    expect(image.getAttribute("src")).toBe(PORTRAIT_MANIFEST.angry.url);
    expect(stage.dataset.expression).toBe("angry");
  });

  it("预加载失败时上报错误，且不把错误抛进渲染循环", async () => {
    const { stage, renderer, onImageLoadError } = setup();
    expect(() => renderer.setExpression(PORTRAIT_MANIFEST.tired)).not.toThrow();

    FakeImage.instances.at(-1)?.rejectLoad();
    await flush();

    expect(onImageLoadError).toHaveBeenCalledTimes(1);
    expect(onImageLoadError.mock.calls[0][0].expression).toEqual(PORTRAIT_MANIFEST.tired);
    expect(stage.dataset.imageState).toBe("error");
    expect(stage.dataset.expressionPending).toBeUndefined();
  });

  it("解码后尺寸为 0 也算失败（拿到字节 ≠ 能显示）", async () => {
    const { renderer, onImageLoadError } = setup();
    renderer.setExpression(PORTRAIT_MANIFEST.surprised);

    FakeImage.instances.at(-1)?.resolveLoad(0, 0);
    await flush();

    expect(onImageLoadError).toHaveBeenCalledTimes(1);
    expect(onImageLoadError.mock.calls[0][0].error.message).toBe("image_decoded_with_empty_size");
  });

  it("已有可见旧图时加载失败不切到 error 视觉（不闪白）", async () => {
    const { stage, image, renderer } = setup();
    // 「是否已有可见图」看的是图片元素自身的状态 + 解码尺寸
    // （jsdom 的 naturalWidth 恒为 0，需要显式模拟已解码）
    Object.defineProperty(image, "naturalWidth", { value: 320, configurable: true });
    image.dataset.imageState = "ready";

    renderer.setExpression(PORTRAIT_MANIFEST.anxious);
    FakeImage.instances.at(-1)?.rejectLoad();
    await flush();

    expect(stage.dataset.imageState).toBe("ready");
  });

  it("动效只改 data-motion，不动图片", async () => {
    const { stage, image, renderer } = setup();
    const srcBefore = image.getAttribute("src");

    expect(renderer.setMotion("speak")).toBe("speak");
    expect(stage.dataset.motion).toBe("speak");
    expect(image.getAttribute("src")).toBe(srcBefore);

    expect(renderer.setMotion("think")).toBe("think");
    expect(stage.dataset.motion).toBe("think");
  });

  it("非法动效回落 idle，而不是把错误抛出去", () => {
    const { stage, renderer } = setup();
    // @ts-expect-error 故意传非法值：渲染器必须容错
    expect(renderer.setMotion("dancing")).toBe("idle");
    expect(stage.dataset.motion).toBe("idle");
  });

  it("restart 会先归零再重设，以便重放同名动效", () => {
    const { stage, renderer } = setup();
    renderer.setMotion("speak");
    renderer.setMotion("speak", { restart: true });
    expect(stage.dataset.motion).toBe("speak");
  });

  it("构图校准写入 transform，传空值可恢复默认", () => {
    const { stage, image, renderer } = setup();

    renderer.setPortraitLayout({ scale: 1.2, offsetX: 4, offsetY: -6, anchor: "bottom_center" });
    expect(image.style.transform).toBe("translate(4px, -6px) scale(1.2)");
    expect(image.style.transformOrigin).toBe("bottom center");
    expect(stage.dataset.layoutApplied).toBe("true");

    renderer.setPortraitLayout(null);
    expect(image.style.transform).toBe("");
    expect(image.style.transformOrigin).toBe("");
    expect(stage.dataset.layoutApplied).toBeUndefined();
  });

  it("非法构图参数回落安全默认", () => {
    const { image, renderer } = setup();
    renderer.setPortraitLayout({ scale: -3, offsetX: Number.NaN, offsetY: Number.NaN });
    expect(image.style.transform).toBe("translate(0px, 0px) scale(1)");
  });

  it("dispose 之后挂起的加载回调不再改动 DOM", async () => {
    const { stage, image, renderer } = setup();
    renderer.setExpression(PORTRAIT_MANIFEST.happy);
    const probe = FakeImage.instances.at(-1);
    const srcBefore = image.getAttribute("src");

    renderer.dispose();
    probe?.resolveLoad();
    await flush();

    expect(image.getAttribute("src")).toBe(srcBefore);
    expect(stage.dataset.expression).toBeUndefined();
    expect(stage.dataset.expressionPending).toBeUndefined();
  });

  it("传 null 立绘时保持现状，不闪回默认图", async () => {
    const { image, renderer } = setup();
    renderer.setExpression(PORTRAIT_MANIFEST.happy);
    FakeImage.instances.at(-1)?.resolveLoad();
    await flush();

    const before = FakeImage.instances.length;
    renderer.setExpression(null);
    await flush();

    expect(FakeImage.instances.length).toBe(before);
    expect(image.getAttribute("src")).toBe(PORTRAIT_MANIFEST.happy.url);
  });
});
