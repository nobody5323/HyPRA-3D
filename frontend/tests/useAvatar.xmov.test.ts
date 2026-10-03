/**
 * `useXmovAvatar`（魔珐 SDK 实现）的**断线退避重连**测试。
 *
 * SDK 用替身注入（`window.XmovAvatar` 存在时 `loadXmovSdk` 会直接复用，
 * 不插入 script 标签），因此无需网络与真实密钥即可覆盖：
 * - 退避节奏（1s → 2s → 4s）与重建次数；
 * - 「init 期间报错、随后却连上」时**不得**把刚就绪的实例销毁重建；
 * - 重连用尽后回落到 `onUnavailable`（页面据此降级为静默）。
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MockInstance } from "vitest";

import { useXmovAvatar } from "@/hooks/avatar/useAvatar";

const CREDENTIALS = { appId: "test-app", appSecret: "test-secret" };

/** 可控的魔珐 SDK 替身：init 的成败由测试决定。 */
class FakeXmovAvatar {
  static instances: FakeXmovAvatar[] = [];

  /** SDK 构造参数：`onMessage` 就在其中（`onError` / `onVoiceStateChange` 则是实例属性） */
  readonly config: Record<string, any>;

  onError: ((error: unknown) => void) | null = null;
  onVoiceStateChange: ((...args: unknown[]) => void) | null = null;
  destroyed = false;
  spoken: string[] = [];

  /**
   * 模拟服务端回的语音事件风格。
   *
   * - `short`（默认）＝ SDK 内部的**真实取值** `"start"` / `"end"`
   *   （源码 EventDispatcher 的 `voice_state_change` 分支）；
   * - `documented` = 官方文档里用事件名描述的写法 `"voice_start"` / `"voice_end"`。
   */
  voiceStateStyle: "short" | "documented" = "short";

  private resolveInit: (() => void) | null = null;
  private rejectInit: ((error: unknown) => void) | null = null;

  constructor(config: Record<string, any> = {}) {
    this.config = config;
    FakeXmovAvatar.instances.push(this);
  }

  /** 测试辅助：模拟 SDK 经 `onMessage` 上报（通知类与错误类走同一个回调） */
  emitMessage(payload: { code: number; message?: string }) {
    this.config.onMessage?.(payload);
  }

  init() {
    return new Promise<void>((resolve, reject) => {
      this.resolveInit = resolve;
      this.rejectInit = reject;
    });
  }

  /** 测试辅助：init 成功（模拟连接建立） */
  succeedInit() {
    this.resolveInit?.();
  }

  /** 测试辅助：init 失败（模拟超时 / 鉴权失败） */
  failInit(error: unknown) {
    this.rejectInit?.(error);
  }

  destroy() {
    this.destroyed = true;
  }

  idle() {}
  listen() {}
  interactiveidle() {}
  interrupt() {}

  speak(payload: string, isStart?: boolean, isEnd?: boolean) {
    this.spoken.push(payload);
    void isStart;
    void isEnd;
    // 模拟服务端很快回语音事件（真实环境约在几十~几百毫秒内）
    setTimeout(() => {
      if (this.voiceStateStyle === "short") {
        this.onVoiceStateChange?.("start", 0, "sid-1");
        this.onVoiceStateChange?.("end", 120, "sid-1");
      } else {
        this.onVoiceStateChange?.("voice_start", 0, "sid-1");
        this.onVoiceStateChange?.("voice_end", 120, "sid-1");
      }
    }, 10);
  }

  static latest() {
    return FakeXmovAvatar.instances[FakeXmovAvatar.instances.length - 1];
  }
}

/** 渲染 hook 并推进到「SDK 已实例化」 */
async function mountAvatar(onUnavailable?: (reason: string) => void) {
  const rendered = renderHook(() =>
    useXmovAvatar("#stage", { credentials: CREDENTIALS, enabled: true, onUnavailable }),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0); // flush 加载 SDK → new XmovAvatar → init()
  });
  return rendered;
}

let warnSpy: MockInstance;

beforeEach(() => {
  FakeXmovAvatar.instances = [];
  vi.stubGlobal("XmovAvatar", FakeXmovAvatar);
  Object.defineProperty(window, "XmovAvatar", {
    value: FakeXmovAvatar,
    configurable: true,
    writable: true,
  });
  vi.useFakeTimers();
  // 重连会打 console.warn（预期日志）：静音以免刷屏淹没真正的失败信息
  warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  // 通知类 onMessage 会打 console.info（预期日志）：同样静音
  vi.spyOn(console, "info").mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useXmovAvatar 断线退避重连", () => {
  it("SDK 报错后按 1s 退避重建，并销毁旧实例", async () => {
    await mountAvatar();
    const first = FakeXmovAvatar.instances[0];
    await act(async () => {
      first.succeedInit();
    });

    act(() => {
      first.onError?.(new Error("socket closed"));
    });
    // 退避期内不重建
    expect(FakeXmovAvatar.instances).toHaveLength(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(FakeXmovAvatar.instances).toHaveLength(2);
    expect(first.destroyed).toBe(true);
  });

  it("退避按 1s → 2s → 4s 递增", async () => {
    await mountAvatar();
    const delays: number[] = [];

    for (let round = 0; round < 3; round += 1) {
      const current = FakeXmovAvatar.latest();
      act(() => {
        current.onError?.(new Error(`round ${round}`));
      });

      // 逐步推进，找出真正触发重建所需的时长
      let elapsed = 0;
      const before = FakeXmovAvatar.instances.length;
      while (FakeXmovAvatar.instances.length === before && elapsed < 10_000) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1_000);
        });
        elapsed += 1_000;
      }
      delays.push(elapsed);
    }

    expect(delays).toEqual([1_000, 2_000, 4_000]);
  });

  it("init 期间报错但随后连上：不把刚就绪的实例销毁重建", async () => {
    await mountAvatar();
    const first = FakeXmovAvatar.instances[0];

    // init 尚未 resolve 时报错（握手期抖动）→ 排下一次重连
    act(() => {
      first.onError?.(new Error("handshake blip"));
    });

    // 紧接着 init 成功：必须清掉已排程的重连，否则 1s 后会拆掉刚就绪的实例
    await act(async () => {
      first.succeedInit();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(FakeXmovAvatar.instances).toHaveLength(1);
    expect(first.destroyed).toBe(false);
  });

  it("重连用尽（3 次）后回落到 onUnavailable 降级", async () => {
    const onUnavailable = vi.fn();
    await mountAvatar(onUnavailable);

    // 连续报错：前 3 次排重连，第 4 次不再排 → 降级
    for (let round = 0; round < 4; round += 1) {
      const current = FakeXmovAvatar.latest();
      act(() => {
        current.onError?.(new Error(`error ${round}`));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(8_000); // 覆盖最长退避
      });
    }

    expect(FakeXmovAvatar.instances).toHaveLength(4); // 初始 + 3 次重建
    expect(onUnavailable).toHaveBeenCalledTimes(1);
    expect(onUnavailable.mock.calls[0][0]).toContain("error 3");
  });
});

/**
 * 回归用例：SDK 把「通知」与「致命错误」走同一个 `onMessage` 回调。
 *
 * 曾经只要消息带 `code` 字段就当致命错误 → 调 `onUnavailable` → 整条魔珐渲染
 * 被判不可用 → 桌宠/Web 端卸载 3D、挂上本地渲染层。
 * 而 `10002 CONNECT_SOCKET_ERROR`（"socket长时间未下发数据"）是**自恢复**的：
 * SDK 紧接着自己 `reStartSDK()` 重连。用户连上后暂时不说话就会触发，
 * 表现就是**「数字人刚连上、几秒后被默认模型顶掉」**（用户实测反馈）。
 */
describe("useXmovAvatar 的 onMessage 分类（通知 vs 致命）", () => {
  it("通知类错误码不降级，仍保持就绪", async () => {
    const onUnavailable = vi.fn();
    const { result } = await mountAvatar(onUnavailable);
    const avatar = FakeXmovAvatar.instances[0];
    await act(async () => {
      avatar.succeedInit();
    });

    act(() => {
      avatar.emitMessage({ code: 10002, message: "Error: socket长时间未下发数据" });
      avatar.emitMessage({ code: 50001, message: "网络断开" });
      avatar.emitMessage({ code: 30005, message: "Error: 身体数据过期" });
      avatar.emitMessage({ code: 20010, message: "Error: 事件中存在不包含speech_id的事件" });
    });

    expect(onUnavailable).not.toHaveBeenCalled();
    expect(result.current.stage).toBe("ready");
    expect(result.current.ready).toBe(true);
    expect(avatar.destroyed).toBe(false);
  });

  it("真正的致命错误码仍然降级", async () => {
    const onUnavailable = vi.fn();
    await mountAvatar(onUnavailable);
    const avatar = FakeXmovAvatar.instances[0];
    await act(async () => {
      avatar.succeedInit();
    });

    act(() => {
      avatar.emitMessage({ code: 30002, message: "表情数据加载失败" });
    });

    expect(onUnavailable).toHaveBeenCalledTimes(1);
    expect(String(onUnavailable.mock.calls[0][0])).toContain("30002");
  });
});

describe("useXmovAvatar 分段播报的语音事件消费", () => {
  /** 断言整段播报**没有**靠超时收尾——即 voice_end 真的被消费了 */
  function expectNoTimeoutFallback() {
    const timeoutWarnings = warnSpy.mock.calls.filter((call) =>
      String(call[0]).includes("未收到 voice_end"),
    );
    expect(timeoutWarnings).toHaveLength(0);
  }

  const PARTS = [
    { text: "第一段。", ssml: "<speak>第一段。</speak>" },
    { text: "第二段。", ssml: "<speak>第二段。</speak>" },
  ];

  /**
   * 回归用例：SDK 回调的 state **实际取值是 `"start"` / `"end"`**（源码证据见 useAvatar.ts）。
   *
   * 若只认文档里的 `"voice_end"`，事件永远匹配不上，每段都只能等超时（≥12s）才收尾——
   * 而最后一段被紧随其后的 `idle()` 切断，就是用户实测到的「最后一段说不完」。
   */
  it('SDK 用内部取值 "end" 回调时逐段推进，不靠超时', async () => {
    const { result } = await mountAvatar();
    const avatar = FakeXmovAvatar.instances[0];
    avatar.voiceStateStyle = "short";
    await act(async () => {
      avatar.succeedInit();
    });

    act(() => {
      void result.current.speakChunks(PARTS);
    });

    // 只给 1.5s 虚拟时间：事件驱动下足够播完两段（每段事件 ~10ms + 段间过渡 400ms）。
    // 若事件未被识别，两段都要等 12s 超时 → 此处断言会立刻失败，
    // 且不留下悬着的 Promise（不会拖坏后续用例）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500);
    });

    expect(avatar.spoken).toEqual(["<speak>第一段。</speak>", "<speak>第二段。</speak>"]);
    expectNoTimeoutFallback();
  });

  /** 兼容性：文档写法（voice_start / voice_end）也必须被识别 */
  it('文档写法 "voice_end" 同样被识别', async () => {
    const { result } = await mountAvatar();
    const avatar = FakeXmovAvatar.instances[0];
    avatar.voiceStateStyle = "documented";
    await act(async () => {
      avatar.succeedInit();
    });

    act(() => {
      void result.current.speakChunks(PARTS);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500);
    });

    expect(avatar.spoken).toHaveLength(2);
    expectNoTimeoutFallback();
  });
});
