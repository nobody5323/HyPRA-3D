/**
 * `useServerTtsAvatar`（服务端 TTS，给 Live2D / 静态立绘出真声音）的行为测试。
 *
 * 重点覆盖四类**真实故障**（每个用例都是对应缺陷的回归测试）：
 * 1. 音频与口型**必须同源**——同一次 `/media/avatar` 请求；
 * 2. 口型时钟必须对齐**音频真正开始播放**的时刻，否则是固定的音画偏移；
 * 3. 降级（无音频）/ 播放被拦 / 打断 / 卸载，每条收尾路径都不得让 `speak` 挂死；
 * 4. 分段播报是**流水线**：逐段合成（首段先出声）+ 播放时预取下一段，字幕与语音同步出现。
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useServerTtsAvatar } from "@/hooks/useAvatar";
import type { SpeechChunk } from "@/hooks/useAvatar";

/** 可精确驱动的 Audio 替身（真实浏览器里 playing / ended / timeupdate 都是异步事件）。 */
class FakeAudio {
  static instances: FakeAudio[] = [];
  /** 设为非空则下一次 play() 失败（模拟自动播放被拦 NotAllowedError） */
  static nextPlayError: Error | null = null;

  src: string;
  currentTime = 0;
  preload = "";
  paused = true;

  private readonly listeners = new Map<string, Set<() => void>>();

  constructor(src: string) {
    this.src = src;
    FakeAudio.instances.push(this);
  }

  addEventListener(type: string, listener: () => void) {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener);
    this.listeners.set(type, set);
  }

  removeEventListener(type: string, listener: () => void) {
    this.listeners.get(type)?.delete(listener);
  }

  removeAttribute(name: string) {
    if (name === "src") this.src = "";
  }

  pause() {
    this.paused = true;
  }

  async play() {
    if (FakeAudio.nextPlayError) throw FakeAudio.nextPlayError;
    this.paused = false;
    // 刻意**不**自动触发 playing：真实浏览器里它稍后才到，
    // 测试显式 emitPlaying() 才能验证「口型从出声那一刻起算」。
  }

  // ---------- 测试驱动 ----------

  emitPlaying() {
    this.emit("playing");
  }

  /** 播放推进到指定秒数（同时触发 timeupdate，驱动分段字幕） */
  seek(seconds: number) {
    this.currentTime = seconds;
    this.emit("timeupdate");
  }

  /** 播完 */
  finish() {
    this.paused = true;
    this.emit("ended");
  }

  private emit(type: string) {
    for (const listener of this.listeners.get(type) ?? []) listener();
  }
}

/** 一帧一个字（与后端估算轨道一致），便于用字符位置推算时间 */
function visemesFor(text: string, stepMs = 200) {
  return Array.from(text).map((char, index) => ({
    start_ms: index * stepMs,
    end_ms: (index + 1) * stepMs,
    viseme: "A",
    char,
  }));
}

/** `/media/avatar` 成功响应（音频 + 同源口型时间轴） */
function avatarBody(text: string, durationMs = 4_000, audioUrl = "/media/audio/tts_abc.wav") {
  return {
    text,
    provider: "gpt_sovits",
    duration_ms: durationMs,
    has_audio: true,
    audio_url: audioUrl,
    audio_format: "wav",
    visemes: visemesFor(text),
    face: [],
    body: [],
    meta: {},
  };
}

/** 按调用顺序依次返回不同响应（分段流水线要区分每段的音频与口型） */
function stubAvatarFetchSeq(bodies: unknown[]) {
  let index = 0;
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => {
    const body = bodies[Math.min(index, bodies.length - 1)];
    index += 1;
    return {
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function stubAvatarFetch(body: unknown) {
  // 显式声明入参类型：否则 `mock.calls[0]` 是空元组，取不到 url / body 做断言
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** speechSynthesis 替身（降级路径用；只保留实现用到的成员） */
class FakeUtterance {
  text: string;
  voice: unknown = null;
  rate = 1;
  onend: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(text: string) {
    this.text = text;
  }
}

class FakeSpeechSynthesis {
  readonly spoken: string[] = [];
  onvoiceschanged: (() => void) | null = null;
  private current: FakeUtterance | null = null;

  speak(utterance: FakeUtterance) {
    this.spoken.push(utterance.text);
    this.current = utterance;
  }

  cancel() {
    const current = this.current;
    this.current = null;
    // 真实浏览器为被取消的语音触发 onend，这里同样收敛 Promise
    current?.onend?.();
  }

  getVoices() {
    return [{ lang: "zh-CN", name: "Fake Xiaoxiao" }];
  }

  /** 让当前这段"念完"（浏览器替身不自动结束，与真实浏览器一致） */
  finishCurrent() {
    const current = this.current;
    this.current = null;
    current?.onend?.();
  }
}

let speech: FakeSpeechSynthesis;

beforeEach(() => {
  FakeAudio.instances = [];
  FakeAudio.nextPlayError = null;
  vi.stubGlobal("Audio", FakeAudio);
  speech = new FakeSpeechSynthesis();
  vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
  Object.defineProperty(window, "speechSynthesis", {
    configurable: true,
    value: speech,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

interface SpeakableController {
  current: { speak: (text: string) => Promise<void> };
}

/**
 * 启动播报但**不等它结束**，返回之后用于收尾的 pending。
 *
 * ⚠️ 刻意用对象包装而不是直接 `return pending`：`async` 函数返回 Promise 时会
 * 采纳（adopt）它的状态，`await startSpeak(...)` 就会一直等到音频播完——测试反而被挂死。
 */
async function startSpeak(
  controller: SpeakableController,
  text: string,
): Promise<{ pending: Promise<void> }> {
  const holder: { pending?: Promise<void> } = {};
  await act(async () => {
    holder.pending = controller.current.speak(text);
  });
  return { pending: holder.pending as Promise<void> };
}

describe("useServerTtsAvatar：音频与口型同源", () => {
  it("一次请求同时拿到音频与口型时间轴（不再另发一次取口型）", async () => {
    const fetchMock = stubAvatarFetch(avatarBody("我在。"));
    const { result } = renderHook(() => useServerTtsAvatar());

    const { pending } = await startSpeak(result, "我在。");

    // ① 只请求一次：音频与口型必须是同一次合成的产物
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/media/avatar");

    // ② 相对地址被补全为后端地址（否则会打到前端 3000 而 404）
    expect(FakeAudio.instances[0].src).toBe("http://localhost:8000/media/audio/tts_abc.wav");

    // ③ 口型此刻还没交给渲染器：要等真正出声，否则是固定的音画偏移
    expect(result.current.timeline).toBeNull();

    await act(async () => {
      FakeAudio.instances[0].emitPlaying();
    });
    expect(result.current.timeline?.map((frame) => frame.char)).toEqual(["我", "在", "。"]);

    await act(async () => {
      FakeAudio.instances[0].finish();
      await pending;
    });
    // 播完即闭嘴（时间轴清空），否则嘴会一直张着
    expect(result.current.timeline).toBeNull();
  });

  it("provider 标记为 server，且初始无时间轴", () => {
    const { result } = renderHook(() => useServerTtsAvatar());
    expect(result.current.provider).toBe("server");
    expect(result.current.timeline).toBeNull();
  });

  it("播放出错也要收尾（不能挂在 speak 里回不到 idle）", async () => {
    stubAvatarFetch(avatarBody("我在。"));
    const { result } = renderHook(() => useServerTtsAvatar());

    const { pending } = await startSpeak(result, "我在。");
    await act(async () => {
      FakeAudio.instances[0].emitPlaying();
      // 音频元素报错（地址 404 / 解码失败）：真实浏览器会触发 error 事件
      FakeAudio.instances[0].finish();
      await pending;
    });

    expect(result.current.timeline).toBeNull();
  });
});

describe("useServerTtsAvatar：降级路径", () => {
  it("后端没给音频时逐句回落浏览器 TTS", async () => {
    stubAvatarFetch({ ...avatarBody("我在。"), has_audio: false, audio_url: null });
    const { result } = renderHook(() => useServerTtsAvatar());

    const { pending } = await startSpeak(result, "我在。");
    // 服务端没音频 → 已改走浏览器 TTS；替身不会自动念完，手动收尾
    expect(speech.spoken).toEqual(["我在。"]);
    await act(async () => {
      speech.finishCurrent();
      await pending;
    });
  });

  it("请求失败（服务未部署）同样回落浏览器 TTS", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("connect refused");
      }),
    );
    const { result } = renderHook(() => useServerTtsAvatar());

    const { pending } = await startSpeak(result, "我在。");
    expect(speech.spoken).toEqual(["我在。"]);
    await act(async () => {
      speech.finishCurrent();
      await pending;
    });
  });

  it("enabled=false 时完全不调后端，直接浏览器 TTS", async () => {
    const fetchMock = stubAvatarFetch(avatarBody("我在。"));
    const { result } = renderHook(() => useServerTtsAvatar({ enabled: false }));

    const { pending } = await startSpeak(result, "我在。");

    expect(fetchMock).not.toHaveBeenCalled();
    expect(speech.spoken).toEqual(["我在。"]);
    expect(result.current.detail).toContain("浏览器");

    await act(async () => {
      speech.finishCurrent();
      await pending;
    });
  });

  it("自动播放被拦：不折腾降级（浏览器 TTS 同样会被拦），立即收尾", async () => {
    stubAvatarFetch(avatarBody("我在。"));
    FakeAudio.nextPlayError = new Error("NotAllowedError");
    const { result } = renderHook(() => useServerTtsAvatar());

    await act(async () => {
      await result.current.speak("我在。"); // 必须立即返回，不挂起
    });

    expect(speech.spoken).toEqual([]); // 不双重出声
    expect(result.current.timeline).toBeNull();
  });

  it("空文本不请求后端（避免一次无意义合成）", async () => {
    const fetchMock = stubAvatarFetch(avatarBody(""));
    const { result } = renderHook(() => useServerTtsAvatar());

    await act(async () => {
      await result.current.speak("   ");
    });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("useServerTtsAvatar：分段播报（流水线：逐段合成 + 预取 + 字幕同步）", () => {
  const chunks: SpeechChunk[] = [
    { text: "我在。", ssml: "<speak>我在。</speak>" },
    { text: "慢慢说。", ssml: "<speak>慢慢说。</speak>" },
  ];

  it("首段先出声：只等第一段合成，不等整段；字幕与音频同时出现", async () => {
    const fetchMock = stubAvatarFetchSeq([
      avatarBody("我在。", 4_000, "/media/audio/seg1.wav"),
      avatarBody("慢慢说。", 4_000, "/media/audio/seg2.wav"),
    ]);
    const { result } = renderHook(() => useServerTtsAvatar());
    const seen: number[] = [];

    let pending!: Promise<void>;
    await act(async () => {
      pending = result.current.speakChunks(chunks, (index) => seen.push(index));
    });

    // ① 只播第一段（不再是"整段合成后一次播完"）
    expect(FakeAudio.instances).toHaveLength(1);
    expect(FakeAudio.instances[0].src).toContain("/media/audio/seg1.wav");
    // ② 已经预取了后续段（共 2 段，全部发出；但只播了第 1 段）
    expect(fetchMock).toHaveBeenCalledTimes(2);
    // ③ 字幕已切到第 1 句，而音频**还没出声**——两者都发生在"该段就绪"这一刻
    expect(seen).toEqual([0]);
    expect(result.current.timeline).toBeNull();

    await act(async () => {
      FakeAudio.instances[0].emitPlaying();
    });
    expect(result.current.timeline?.map((frame) => frame.char)).toEqual(["我", "在", "。"]);

    await act(async () => {
      FakeAudio.instances[0].finish();
    });
    // ④ 第 1 段播完 → 第 2 段立即接手（预取过，无需再等），字幕同步推进
    expect(seen).toEqual([0, 1]);
    expect(FakeAudio.instances).toHaveLength(2);
    expect(FakeAudio.instances[1].src).toContain("/media/audio/seg2.wav");

    await act(async () => {
      FakeAudio.instances[1].emitPlaying();
      FakeAudio.instances[1].finish();
      await pending;
    });
    expect(seen).toEqual([0, 1]);
  });

  it("预取深度固定为 1 段（实测更深会拖慢首段）", async () => {
    const fetchMock = stubAvatarFetchSeq([
      avatarBody("我在。", 4_000, "/media/audio/seg1.wav"),
      avatarBody("慢慢说。", 4_000, "/media/audio/seg2.wav"),
      avatarBody("我在听。", 4_000, "/media/audio/seg3.wav"),
      avatarBody("嗯，好。", 4_000, "/media/audio/seg4.wav"),
    ]);
    const { result } = renderHook(() => useServerTtsAvatar());
    const four: SpeechChunk[] = [
      ...chunks,
      { text: "我在听。", ssml: "<speak>我在听。</speak>" },
      { text: "嗯，好。", ssml: "<speak>嗯，好。</speak>" },
    ];

    let pending!: Promise<void>;
    await act(async () => {
      pending = result.current.speakChunks(four, undefined);
    });

    // 第 1 段在播：只预取了第 2 段（第 3、4 段还没发）→ 不会给服务端制造并发
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(FakeAudio.instances).toHaveLength(1);

    await act(async () => {
      FakeAudio.instances[0].finish(); // 取走第 2 段 → 补上第 3 段
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);

    await act(async () => {
      FakeAudio.instances[1].emitPlaying();
      FakeAudio.instances[1].finish();
    });
    expect(fetchMock).toHaveBeenCalledTimes(4); // 再补第 4 段

    await act(async () => {
      FakeAudio.instances[2].emitPlaying();
      FakeAudio.instances[2].finish();
    });
    await act(async () => {
      FakeAudio.instances[3].emitPlaying();
      FakeAudio.instances[3].finish();
      await pending;
    });

    expect(FakeAudio.instances).toHaveLength(4);
  });


  it("某一段拿不到音频时只降级那一段，前后段仍走服务端", async () => {
    const fetchMock = stubAvatarFetchSeq([
      avatarBody("我在。", 4_000, "/media/audio/seg1.wav"),
      { ...avatarBody("慢慢说。"), has_audio: false, audio_url: null },
      avatarBody("我在听。", 4_000, "/media/audio/seg3.wav"),
    ]);
    const { result } = renderHook(() => useServerTtsAvatar());
    const seen: number[] = [];

    let pending!: Promise<void>;
    await act(async () => {
      pending = result.current.speakChunks(
        [...chunks, { text: "我在听。", ssml: "<speak>我在听。</speak>" }],
        (index) => seen.push(index),
      );
    });
    await act(async () => {
      FakeAudio.instances[0].emitPlaying();
      FakeAudio.instances[0].finish();
    });

    // 中间那段用浏览器 TTS 顶上（此刻它已"在说"），字幕照常推进
    expect(speech.spoken).toEqual(["慢慢说。"]);
    expect(seen).toEqual([0, 1]);

    await act(async () => {
      speech.finishCurrent(); // 浏览器那段念完 → 循环继续到第 3 段
    });
    await act(async () => {
      FakeAudio.instances[1].emitPlaying();
      FakeAudio.instances[1].finish();
      await pending;
    });

    // 三段都请求过后端；但只有 2 段拿到了音频（第 1、3 段），第 2 段由浏览器语音顶上。
    // 注：不能用 audio.src 断言——播完会调 stopAudio() 释放（removeAttribute("src")）。
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(FakeAudio.instances).toHaveLength(2);
    expect(seen).toEqual([0, 1, 2]);
  });

  it("整段播报（单段）仍走 speak 路径，不受流水线影响", async () => {
    const fetchMock = stubAvatarFetch(avatarBody("我在。"));
    const { result } = renderHook(() => useServerTtsAvatar());

    const { pending } = await startSpeak(result, "我在。");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      FakeAudio.instances[0].finish();
      await pending;
    });
  });
});


describe("useServerTtsAvatar：打断与卸载", () => {
  it("打断会停掉音频、清空口型，并让 speak 立即返回（不等到超时）", async () => {
    stubAvatarFetch(avatarBody("我在。"));
    const { result } = renderHook(() => useServerTtsAvatar());

    const { pending } = await startSpeak(result, "我在。");
    await act(async () => {
      FakeAudio.instances[0].emitPlaying();
    });
    expect(result.current.timeline).not.toBeNull();

    await act(async () => {
      result.current.interrupt();
      await pending; // 若不监听 abort，这里会挂到 9s 后的超时
    });

    expect(FakeAudio.instances[0].paused).toBe(true);
    expect(result.current.timeline).toBeNull();
  });

  it("组件卸载后音频必须停下（否则会继续出声）", async () => {
    stubAvatarFetch(avatarBody("我在。"));
    const { result, unmount } = renderHook(() => useServerTtsAvatar());

    await startSpeak(result, "我在。");
    const audio = FakeAudio.instances[0];
    await act(async () => {
      audio.emitPlaying();
    });

    unmount();

    expect(audio.paused).toBe(true);
    expect(audio.src).toBe("");
  });
});

describe("useServerTtsAvatar：超时兜底", () => {
  it("迟迟收不到 ended 时按「音频时长 + 宽限」收尾", async () => {
    vi.useFakeTimers();
    try {
      stubAvatarFetch(avatarBody("我在。", 4_000));
      const { result } = renderHook(() => useServerTtsAvatar());

      let pending!: Promise<void>;
      await act(async () => {
        pending = result.current.speak("我在。");
        await vi.advanceTimersByTimeAsync(0); // 走完 fetch 与 play
      });
      await act(async () => {
        FakeAudio.instances[0].emitPlaying();
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(9_000); // 4000（时长）+ 5000（宽限）
        await pending;
      });

      expect(result.current.timeline).toBeNull();
      expect(FakeAudio.instances[0].paused).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
