/**
 * Live2D 渲染器测试（用**假桥**驱动，不需要 SDK / 模型 / WebGL）。
 *
 * 这正是把 Cubism 收敛成一层桥的意义：渲染器的全部行为都能在 CI 与评审环境里验证。
 * 覆盖的行为都是真实故障模式：
 * - 没装 SDK 时不许进入加载流程（要立刻降级）；
 * - 模型就绪前到达的情绪必须补播（否则首屏最后一句情绪丢失）；
 * - 预设为空时**不切表情**（不臆造模型没有的表情）；
 * - 加载过期（dispose / 重新加载）时必须释放 WebGL 资源，不能泄漏；
 * - 渲染中断要停帧循环并上报，而不是每帧刷屏。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createLive2DRenderer,
  type Live2DRendererOptions,
} from "@/lib/live2d/live2d-renderer";
import type { CubismBridge, CubismModelHandle } from "@/lib/live2d/cubism-bridge";
import type { Live2DPresets } from "@/lib/live2d/config";
import type { Live2DModelEntry } from "@/lib/live2d/model-assets";

const PRESETS: Live2DPresets = {
  emotionExpressions: {
    happy: "开心",
    calm: "",
    sad: "",
    anxious: "",
    tired: "",
    angry: "",
    surprised: "",
    neutral: "",
  },
  motionGroups: { idle: "Idle", think: "Think" },
};

const MODEL: Live2DModelEntry = {
  id: "default",
  name: "默认模型",
  modelUrl: "/live2d/default/pet.model3.json",
};

/** 假模型句柄：记录每一次调用，供断言 */
function createFakeHandle(options: { parameterNames?: string[]; playExpression?: boolean; playMotion?: boolean; failUpdate?: boolean } = {}) {
  const handle: CubismModelHandle = {
    setParameter: vi.fn(() => true),
    getParameter: vi.fn(() => 0),
    playExpression: vi.fn(() => options.playExpression ?? true),
    resetExpression: vi.fn(),
    playMotion: vi.fn(() => options.playMotion ?? true),
    update: vi.fn(() => {
      if (options.failUpdate) throw new Error("WebGL 上下文已丢失");
    }),
    resize: vi.fn(),
    parameterNames: vi.fn(() => options.parameterNames ?? ["ParamAngleX", "ParamMouthOpenY"]),
    release: vi.fn(),
  };
  return handle;
}

/** 假桥：可控制加载成功/失败/挂起 */
function createFakeBridge(config: {
  available?: boolean;
  handle?: CubismModelHandle;
  loadError?: Error;
} = {}) {
  const handle = config.handle ?? createFakeHandle();
  let settle: (value: CubismModelHandle) => void = () => {};
  let reject: (error: Error) => void = () => {};
  const load = vi.fn((request: unknown) => {
    void request;
    if (config.loadError) return Promise.reject(config.loadError);
    if (config.available === false) return Promise.reject(new Error("unavailable"));
    return new Promise<CubismModelHandle>((resolve, fail) => {
      settle = resolve;
      reject = fail;
    });
  });
  const bridge: CubismBridge = {
    available: config.available ?? true,
    unavailableReason: config.available === false ? "未安装 Cubism SDK" : "",
    init: vi.fn(),
    load,
  };
  return {
    bridge,
    handle,
    load,
    /**
     * 让加载成功落地（异步）。
     *
     * 必须先等 `load()` 真的被发起：渲染器在调用它之前还要 `await loadCore()`，
     * 同步调用时 settle 函数可能尚未绑定。
     */
    resolveLoad: async () => {
      for (let i = 0; i < 20 && load.mock.calls.length === 0; i += 1) {
        await Promise.resolve();
      }
      settle(handle);
      await flush();
    },
    rejectLoad: async (error: Error) => {
      for (let i = 0; i < 20 && load.mock.calls.length === 0; i += 1) {
        await Promise.resolve();
      }
      reject(error);
      await flush();
    },
  };
}

function setup(
  config: Parameters<typeof createFakeBridge>[0] & {
    loadCore?: () => Promise<void>;
    loadBridge?: () => Promise<CubismBridge>;
  } = {},
) {
  const stage = document.createElement("div");
  const canvas = document.createElement("canvas");
  document.body.append(stage, canvas);

  const fake = createFakeBridge(config);
  const frames: Array<(time: number) => void> = [];
  const onError = vi.fn();
  const onStageChange = vi.fn();
  /** 加载顺序：用于验证 Core → 桥 → SDK 初始化这条不能乱 */
  const order: string[] = [];

  // 包一层桥：记录顺序；`available` 用 getter 透传
  const bridge: CubismBridge = {
    get available() {
      return fake.bridge.available;
    },
    get unavailableReason() {
      return fake.bridge.unavailableReason;
    },
    init: vi.fn(() => {
      order.push("init");
      fake.bridge.init();
    }),
    load: fake.bridge.load,
  };

  const options: Live2DRendererOptions = {
    stage,
    canvas,
    model: MODEL,
    presets: PRESETS,
    onError,
    onStageChange,
    loadCore: async () => {
      order.push("core");
      await (config.loadCore ?? (() => Promise.resolve()))();
    },
    // 真实场景里这里是 `import("@cubism-bridge")`
    loadBridge: async () => {
      order.push("bridge");
      return (config.loadBridge ?? (() => Promise.resolve(bridge)))();
    },
    requestFrame: (callback) => {
      frames.push(callback);
      return frames.length;
    },
    cancelFrame: vi.fn(),
    now: () => 0,
  };

  const renderer = createLive2DRenderer(options);
  return {
    stage,
    canvas,
    renderer,
    onError,
    onStageChange,
    frames,
    ...fake,
    // 放在 ...fake 之后：覆盖 fake 里的原始 bridge（顺序与外层包装都要生效）
    order,
    bridge,
    /** 跑一帧（模拟 rAF 回调） */
    runFrame(time = 16) {
      const callback = frames.shift();
      callback?.(time);
    },
  };
}

/** 冲掉加载 promise 链 */
function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("未安装 SDK 的路径", () => {
  it("bridge 不可用时立刻失败降级，且不发起加载", async () => {
    const ctx = setup({ available: false });
    await flush();

    expect(ctx.load).not.toHaveBeenCalled();
    expect(ctx.renderer.loadStage).toBe("failed");
    expect(ctx.renderer.detail).toContain("Cubism SDK");
    expect(ctx.stage.dataset.live2dStage).toBe("failed");
    expect(ctx.renderer.getStatus().capabilities.live2d).toBe(true);
  });
});

describe("Cubism Core 加载", () => {
  it("顺序必须是 Core → 桥 → SDK 初始化（Framework 模块求值就读全局对象）", async () => {
    const ctx = setup();
    await flush();

    expect(ctx.order).toEqual(["core", "bridge", "init"]);
  });

  it("Core 加载失败时直接降级，不碰 SDK、也不加载模型", async () => {
    const ctx = setup({
      loadCore: () => Promise.reject(new Error("Cubism Core 脚本加载失败：404")),
    });
    await flush();

    expect(ctx.renderer.loadStage).toBe("failed");
    expect(ctx.renderer.detail).toContain("Core");
    expect(ctx.onError).toHaveBeenCalledTimes(1);
    expect(ctx.bridge.init).not.toHaveBeenCalled();
    expect(ctx.load).not.toHaveBeenCalled();
  });
});

describe("加载与帧循环", () => {
  it("加载成功进入 ready，并启动帧循环推进模型", async () => {
    const ctx = setup();
    expect(ctx.renderer.loadStage).toBe("loading");
    await flush();
    expect(ctx.bridge.init).toHaveBeenCalledTimes(1);

    await ctx.resolveLoad();

    expect(ctx.load).toHaveBeenCalledWith(
      expect.objectContaining({ canvas: ctx.canvas, modelUrl: MODEL.modelUrl }),
    );
    expect(ctx.renderer.loadStage).toBe("ready");
    expect(ctx.stage.dataset.live2dStage).toBe("ready");

    ctx.runFrame(16);
    expect(ctx.handle.update).toHaveBeenCalledTimes(1);
    // 帧循环会自续（每帧结束后重新请求下一帧）
    expect(ctx.frames.length).toBe(1);
  });

  it("delta 上限 0.1s：切回标签页时不出现一次性大跳", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    ctx.runFrame(16);
    ctx.runFrame(16 + 5_000); // 模拟标签页挂起 5 秒
    const delta = (ctx.handle.update as ReturnType<typeof vi.fn>).mock.calls[1][0];
    expect(delta).toBeLessThanOrEqual(0.1);
  });

  it("加载失败时上报错误并标记 failed，不进入帧循环", async () => {
    const ctx = setup({ loadError: new Error("模型文件加载失败（404）") });
    await flush();

    expect(ctx.renderer.loadStage).toBe("failed");
    expect(ctx.renderer.detail).toContain("404");
    expect(ctx.onError).toHaveBeenCalledTimes(1);
    expect(ctx.frames).toHaveLength(0);
  });

  it("渲染中断：停掉帧循环并上报（不每帧刷屏）", async () => {
    const ctx = setup({ handle: createFakeHandle({ failUpdate: true }) });
    await ctx.resolveLoad();

    ctx.runFrame(16);

    expect(ctx.onError).toHaveBeenCalledTimes(1);
    expect(ctx.renderer.loadStage).toBe("failed");
    expect(ctx.frames).toHaveLength(0); // 没有继续排下一帧
  });
});

describe("表情映射", () => {
  it("情绪按 presets 映射成模型表情名后播放", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    ctx.renderer.setExpression({ id: "happy", name: "开心" });
    expect(ctx.handle.playExpression).toHaveBeenCalledWith("开心");
    expect(ctx.stage.dataset.expression).toBe("happy");
    expect(ctx.renderer.getStatus().expression).toEqual({ id: "happy", name: "开心" });
  });

  it("预设留空的情绪不切表情（保持中性脸，而不是保持上一张脸）", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    ctx.renderer.setExpression({ id: "sad", name: "难过" });
    expect(ctx.handle.playExpression).not.toHaveBeenCalled();
  });

  it("从表情切回无表情时会把上一张脸收回去（否则表情残留）", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    // happy 在预设里有表情名 → 真的挂上一张脸
    ctx.renderer.setExpression({ id: "happy", name: "开心" });
    expect(ctx.handle.resetExpression).not.toHaveBeenCalled();

    // sad 在预设里留空 → 应显示中性脸，因此必须重置
    ctx.renderer.setExpression({ id: "sad", name: "难过" });
    expect(ctx.handle.resetExpression).toHaveBeenCalledTimes(1);
  });

  it("从未挂过表情时不做无谓的重置", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    ctx.renderer.setExpression({ id: "sad", name: "难过" });
    ctx.renderer.setExpression({ id: "neutral", name: "中性" });

    expect(ctx.handle.resetExpression).not.toHaveBeenCalled();
  });

  it("切回有表情的情绪后，重置标记会重新挂上", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    ctx.renderer.setExpression({ id: "happy", name: "开心" });
    ctx.renderer.setExpression({ id: "sad", name: "难过" }); // → reset
    ctx.renderer.setExpression({ id: "happy", name: "开心" }); // → 重新播放

    expect(ctx.handle.playExpression).toHaveBeenCalledTimes(2);
    expect(ctx.handle.resetExpression).toHaveBeenCalledTimes(1);
  });

  it("模型没有该表情时记录诊断，不抛错、不上报错误", async () => {
    const ctx = setup({ handle: createFakeHandle({ playExpression: false }) });
    await ctx.resolveLoad();

    ctx.renderer.setExpression({ id: "happy", name: "开心" });

    expect(ctx.onError).not.toHaveBeenCalled();
    expect(ctx.renderer.detail).toContain("presets.json");
  });

  it("模型就绪前到达的情绪会补播（首屏情绪不丢）", async () => {
    const ctx = setup();
    // 尚未 resolveLoad：此刻设置表情
    ctx.renderer.setExpression({ id: "happy", name: "开心" });
    expect(ctx.handle.playExpression).not.toHaveBeenCalled();

    await ctx.resolveLoad();

    expect(ctx.handle.playExpression).toHaveBeenCalledWith("开心");
  });

  it("同一表情重复设置不重复播放（幂等）", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    ctx.renderer.setExpression({ id: "happy", name: "开心" });
    ctx.renderer.setExpression({ id: "happy", name: "开心" });

    expect(ctx.handle.playExpression).toHaveBeenCalledTimes(1);
  });

  it("传 null 保持现状（不闪回默认表情）", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    ctx.renderer.setExpression({ id: "happy", name: "开心" });
    ctx.renderer.setExpression(null);

    expect(ctx.renderer.getStatus().expression?.id).toBe("happy");
  });
});

describe("动作与构图", () => {
  it("非 idle 状态播放预设动作组", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    ctx.renderer.setMotion("think");
    expect(ctx.handle.playMotion).toHaveBeenCalledWith("Think", 0, true);
    expect(ctx.stage.dataset.motion).toBe("think");
  });

  it("回到 idle 不打断（让模型自带的 Idle 循环继续）", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    ctx.renderer.setMotion("think");
    (ctx.handle.playMotion as ReturnType<typeof vi.fn>).mockClear();
    ctx.renderer.setMotion("idle");

    expect(ctx.handle.playMotion).not.toHaveBeenCalled();
    expect(ctx.stage.dataset.motion).toBe("idle");
  });

  it("预设缺少动作组时回落 Idle 并给出诊断，不抛错", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    ctx.renderer.setMotion("speak"); // PRESETS 里没有 speak
    expect(ctx.onError).not.toHaveBeenCalled();
    expect(ctx.handle.playMotion).toHaveBeenCalledWith("Idle", 0, true);
    // 静默回落会让用户以为"动作坏了"，所以必须说明是配置没覆盖
    expect(ctx.renderer.detail).toContain("未配置");
  });

  it("构图校准写入 canvas 样式并触发重算投影", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    ctx.renderer.setPortraitLayout({ scale: 1.5, offsetX: 2, offsetY: -8, anchor: "bottom_center" });
    expect(ctx.canvas.style.transform).toBe("translate(2px, -8px) scale(1.5)");
    expect(ctx.canvas.style.transformOrigin).toBe("bottom center");
    expect(ctx.handle.resize).toHaveBeenCalled();
    expect(ctx.stage.dataset.layoutApplied).toBe("true");

    ctx.renderer.setPortraitLayout(null);
    expect(ctx.canvas.style.transform).toBe("");
    expect(ctx.stage.dataset.layoutApplied).toBeUndefined();
  });

  it("非法构图参数回落安全默认", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    ctx.renderer.setPortraitLayout({ scale: -1, offsetX: Number.NaN, offsetY: Number.NaN });
    expect(ctx.canvas.style.transform).toBe("translate(0px, 0px) scale(1)");
  });
});

describe("释放与模式", () => {
  it("dispose 之后到达的加载结果会被立刻释放（不泄漏 WebGL 资源）", async () => {
    const ctx = setup();
    await flush(); // 先让 Core 加载完、load 已发起（即"加载进行中"）
    ctx.renderer.dispose();

    await ctx.resolveLoad();

    expect(ctx.handle.release).toHaveBeenCalledTimes(1);
    expect(ctx.renderer.loadStage).not.toBe("ready");
  });

  it("dispose 会停止帧循环并释放模型", async () => {
    const ctx = setup();
    await ctx.resolveLoad();
    ctx.runFrame(16);

    ctx.renderer.dispose();

    expect(ctx.handle.release).toHaveBeenCalledTimes(1);
    // 帧队列里可能还留着已排定的回调（cancelFrame 只负责取消）——
    // 关键是执行它也不会再推进模型。
    const updateCalls = (ctx.handle.update as ReturnType<typeof vi.fn>).mock.calls.length;
    ctx.runFrame(32);
    expect((ctx.handle.update as ReturnType<typeof vi.fn>).mock.calls.length).toBe(updateCalls);
  });

  it("请求别的渲染模式时不假装成功", () => {
    const ctx = setup({ available: false });
    expect(ctx.renderer.setMode("static_portrait")).toBe("live2d");
    expect(ctx.stage.dataset.rendererRequestedMode).toBe("static_portrait");
  });

  it("暴露模型参数名，供诊断映射是否命中", async () => {
    const ctx = setup({ handle: createFakeHandle({ parameterNames: ["ParamAngleX", "ParamBreath"] }) });
    await ctx.resolveLoad();

    expect(ctx.renderer.parameterNames()).toEqual(["ParamAngleX", "ParamBreath"]);
    expect(ctx.renderer.detail).toContain("未命中");
  });
});

describe("口型联动", () => {
  /** 两段口型：A（大张口）→ I（扁口） */
  const TIMELINE = [
    { start_ms: 0, end_ms: 100, viseme: "A", char: "啊" },
    { start_ms: 100, end_ms: 200, viseme: "I", char: "衣" },
  ];

  it("按时间轴采样嘴部参数，且写在 handle.update 之前", async () => {
    const ctx = setup();
    await ctx.resolveLoad();
    (ctx.handle.setParameter as ReturnType<typeof vi.fn>).mockClear();

    ctx.renderer.setLipSync(TIMELINE);
    ctx.runFrame(50); // 起点为 now()=0 → 经过 50ms，落在 A 段

    expect(ctx.handle.setParameter).toHaveBeenCalledWith("ParamMouthOpenY", 1);
    expect(ctx.handle.setParameter).toHaveBeenCalledWith("ParamMouthForm", 0);

    // 顺序很关键：update 之后再设参数会晚一帧
    const setOrder = (ctx.handle.setParameter as ReturnType<typeof vi.fn>).mock
      .invocationCallOrder[0];
    const updateOrder = (ctx.handle.update as ReturnType<typeof vi.fn>).mock
      .invocationCallOrder[0];
    expect(setOrder).toBeLessThan(updateOrder);
  });

  it("时间推进后采样到下一段口型（扁口）", async () => {
    const ctx = setup();
    await ctx.resolveLoad();

    ctx.renderer.setLipSync(TIMELINE);
    ctx.runFrame(150);

    expect(ctx.handle.setParameter).toHaveBeenCalledWith("ParamMouthForm", -0.6);
  });

  it("setLipSync(null) 立刻闭嘴，不等下一帧", async () => {
    const ctx = setup();
    await ctx.resolveLoad();
    (ctx.handle.setParameter as ReturnType<typeof vi.fn>).mockClear();

    ctx.renderer.setLipSync(null);

    expect(ctx.handle.setParameter).toHaveBeenCalledWith("ParamMouthOpenY", 0);
    expect(ctx.handle.setParameter).toHaveBeenCalledWith("ParamMouthForm", 0);
  });

  it("空时间轴等同于停止口型", async () => {
    const ctx = setup();
    await ctx.resolveLoad();
    (ctx.handle.setParameter as ReturnType<typeof vi.fn>).mockClear();

    ctx.renderer.setLipSync([]);
    ctx.runFrame(50);

    // 只应发生“立刻闭嘴”那一次，不应每帧持续写参数
    expect(ctx.handle.setParameter).toHaveBeenCalledTimes(2);
  });

  it("没有时间轴时不去碰嘴部参数（让模型的默认行为生效）", async () => {
    const ctx = setup();
    await ctx.resolveLoad();
    (ctx.handle.setParameter as ReturnType<typeof vi.fn>).mockClear();

    ctx.runFrame(50);

    expect(ctx.handle.setParameter).not.toHaveBeenCalled();
  });

  it("能力声明里口型已开启", async () => {
    const ctx = setup();
    await ctx.resolveLoad();
    expect(ctx.renderer.getStatus().capabilities.lipSync).toBe(true);
  });
});

describe("阶段上报", () => {
  it("loading → ready 的变化都会上报给宿主", async () => {
    const ctx = setup();
    expect(ctx.onStageChange).toHaveBeenCalledWith("loading", expect.any(String));

    await ctx.resolveLoad();

    expect(ctx.onStageChange).toHaveBeenLastCalledWith("ready", expect.any(String));
  });
});

beforeEach(() => {
  vi.clearAllMocks();
});
