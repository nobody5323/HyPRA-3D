"use client";

/**
 * 感知输入的入口测试：语音（`useSpeechInput`）与发图（`useImageShare`）。
 *
 * 两条贯穿性口径都在这里钉住：
 * 1. **不自动发送** —— 结果只经 `onText` / `onReady` 交给调用方填输入框；
 * 2. **能力不可用就不渲染入口** —— `available` 为 false 时调用方不画按钮，
 *    而不是画一个点了会报错的。
 *
 * 另外验两个「用户会读成功能坏了」的失败模式：麦克风权限被拒、
 * 图片理解没配 Key——两者的提示都必须是**能照做的一句话**。
 */

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, describeImage, transcribeAudio } from "@/lib/api/client";
import { imageShareSupported, useImageShare } from "@/hooks/perception/useImageShare";
import { speechInputSupported, useSpeechInput } from "@/hooks/perception/useSpeechInput";
vi.mock("@/lib/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/client")>("@/lib/api/client");
  return {
    ...actual,
    transcribeAudio: vi.fn(),
    describeImage: vi.fn(),
  };
});

const mockedTranscribe = vi.mocked(transcribeAudio);
const mockedDescribe = vi.mocked(describeImage);

/** 最小的 MediaRecorder 替身：start 后由测试触发 dataavailable / stop。 */
class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  static supported = true;

  state: "inactive" | "recording" = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;

  constructor(public stream: unknown, public options?: unknown) {
    FakeMediaRecorder.instances.push(this);
  }

  static isTypeSupported() {
    return FakeMediaRecorder.supported;
  }

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob([new Uint8Array([1, 2, 3])], { type: this.mimeType }) });
    this.onstop?.();
  }
}

const trackStop = vi.fn();

function installRecorderEnvironment(options: { deny?: boolean } = {}) {
  FakeMediaRecorder.instances = [];
  (globalThis as { MediaRecorder?: unknown }).MediaRecorder = FakeMediaRecorder;
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia: options.deny
        ? vi.fn(async () => {
            throw new Error("NotAllowedError");
          })
        : vi.fn(async () => ({ getTracks: () => [{ stop: trackStop }] })),
    },
  });
}

function removeRecorderEnvironment() {
  delete (globalThis as { MediaRecorder?: unknown }).MediaRecorder;
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: undefined });
}

beforeEach(() => {
  installRecorderEnvironment();
  trackStop.mockClear();
  mockedTranscribe.mockReset();
  mockedDescribe.mockReset();
});

afterEach(() => {
  cleanup();
  removeRecorderEnvironment();
});

// =============================================================
// 语音输入
// =============================================================

describe("speechInputSupported", () => {
  it("有 MediaRecorder 与 getUserMedia 时可用", () => {
    expect(speechInputSupported()).toBe(true);
  });

  it("缺 MediaRecorder 时不可用", () => {
    removeRecorderEnvironment();
    expect(speechInputSupported()).toBe(false);
  });
});

describe("useSpeechInput", () => {
  it("enabled=false 时 available 为 false（后端没启用就不画麦克风）", () => {
    const { result } = renderHook(() =>
      useSpeechInput({ onText: vi.fn(), enabled: false }),
    );
    expect(result.current.available).toBe(false);
  });

  it("权限被拒时给出可照做的提示，且不抛错", async () => {
    installRecorderEnvironment({ deny: true });
    const { result } = renderHook(() => useSpeechInput({ onText: vi.fn() }));

    await act(async () => {
      await result.current.start();
    });

    expect(result.current.recording).toBe(false);
    expect(result.current.error).toContain("麦克风权限");
  });

  it("录音结束后转写，并把结果交给 onText（不自动发送）", async () => {
    mockedTranscribe.mockResolvedValue({
      text: "今天有点累",
      provider: "fake",
      language: "zh",
      duration_seconds: 1,
      warnings: [],
      has_text: true,
    });
    const onText = vi.fn();
    const { result } = renderHook(() => useSpeechInput({ onText }));

    await act(async () => {
      await result.current.start();
    });
    expect(result.current.recording).toBe(true);

    await act(async () => {
      result.current.stop();
      await Promise.resolve();
    });

    await waitFor(() => expect(onText).toHaveBeenCalledWith("今天有点累"));
    // 只回填，不发送——发送是调用方（输入框）的事
    expect(mockedTranscribe).toHaveBeenCalledTimes(1);
  });

  it("取消录音后**不上传**（用户改主意了）", async () => {
    const onText = vi.fn();
    const { result } = renderHook(() => useSpeechInput({ onText }));

    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      result.current.cancel();
      await Promise.resolve();
    });

    expect(mockedTranscribe).not.toHaveBeenCalled();
    expect(onText).not.toHaveBeenCalled();
    // 麦克风必须被释放：否则浏览器标签页会一直显示「正在录音」
    expect(trackStop).toHaveBeenCalled();
  });

  it("转写成功但没内容时给「没识别到」而不是报错", async () => {
    mockedTranscribe.mockResolvedValue({
      text: "",
      provider: "fake",
      language: "",
      duration_seconds: 0,
      warnings: ["没有识别到语音内容。"],
      has_text: false,
    });
    const onText = vi.fn();
    const { result } = renderHook(() => useSpeechInput({ onText }));

    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      result.current.stop();
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.error).toContain("没有识别到"));
    expect(onText).not.toHaveBeenCalled();
  });

  it("后端报错时原样展示 detail（里面写着该把模型放到哪）", async () => {
    mockedTranscribe.mockRejectedValue(
      new ApiError(400, null, "请把模型放到 <数据目录>/models/whisper/small/"),
    );
    const { result } = renderHook(() => useSpeechInput({ onText: vi.fn() }));

    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      result.current.stop();
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.error).toContain("models/whisper"));
  });

  it("clearError 能清掉提示", async () => {
    installRecorderEnvironment({ deny: true });
    const { result } = renderHook(() => useSpeechInput({ onText: vi.fn() }));

    await act(async () => {
      await result.current.start();
    });
    expect(result.current.error).toBeTruthy();

    act(() => result.current.clearError());
    expect(result.current.error).toBeNull();
  });
});

// =============================================================
// 发图
// =============================================================

/** 从挂在 body 上的隐藏 input 里取出文件选择器（hook 自己创建的） */
function hiddenFileInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error("未找到隐藏的文件选择器");
  return input;
}

function pickFile(file: File) {
  const input = hiddenFileInput();
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  input.dispatchEvent(new Event("change"));
}

describe("imageShareSupported", () => {
  it("浏览器环境可用", () => {
    expect(imageShareSupported()).toBe(true);
  });
});

describe("useImageShare", () => {
  it("enabled=false 时 available 为 false（没配 VLM 就不画发图入口）", () => {
    const { result } = renderHook(() => useImageShare({ onReady: vi.fn(), enabled: false }));
    expect(result.current.available).toBe(false);
  });

  it("成功时把描述交给 onReady（不自动发送）", async () => {
    mockedDescribe.mockResolvedValue({
      description: "窗边有一只橘猫",
      provider: "fake",
      model: "fake-vl",
      warnings: [],
      perception_text: "- 用户分享了一张图片，画面里是：窗边有一只橘猫",
    });
    const onReady = vi.fn();
    const { result } = renderHook(() => useImageShare({ onReady }));

    await act(async () => {
      pickFile(new File([new Uint8Array([1])], "cat.png", { type: "image/png" }));
      await Promise.resolve();
    });

    await waitFor(() => expect(onReady).toHaveBeenCalledWith("窗边有一只橘猫"));
    expect(mockedDescribe).toHaveBeenCalledTimes(1);
    expect(result.current.analyzing).toBe(false);
  });

  it("非图片类型直接拒绝，不发请求", async () => {
    const { result } = renderHook(() => useImageShare({ onReady: vi.fn() }));

    await act(async () => {
      pickFile(new File([new Uint8Array([1])], "note.txt", { type: "text/plain" }));
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.error).toContain("只支持"));
    expect(mockedDescribe).not.toHaveBeenCalled();
  });

  it("超过大小上限直接拒绝，不发请求", async () => {
    const { result } = renderHook(() => useImageShare({ onReady: vi.fn() }));

    await act(async () => {
      // 构造一个「声称 9MB」的文件（不真的分配 9MB）
      const big = new File([new Uint8Array([1])], "big.png", { type: "image/png" });
      Object.defineProperty(big, "size", { value: 9 * 1024 * 1024 });
      pickFile(big);
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.error).toContain("太大"));
    expect(mockedDescribe).not.toHaveBeenCalled();
  });

  it("理解成功但没看出内容时给可读提示，而不是当成失败", async () => {
    mockedDescribe.mockResolvedValue({
      description: "   ",
      provider: "fake",
      model: "fake-vl",
      warnings: [],
      perception_text: "",
    });
    const onReady = vi.fn();
    const { result } = renderHook(() => useImageShare({ onReady }));

    await act(async () => {
      pickFile(new File([new Uint8Array([1])], "x.png", { type: "image/png" }));
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.error).toContain("换一张"));
    expect(onReady).not.toHaveBeenCalled();
  });

  it("后端报错时原样展示 detail（里面写着怎么配 Key）", async () => {
    mockedDescribe.mockRejectedValue(
      new ApiError(503, null, "图片理解不可用。请在「能力中心 → 图片理解」里选择 Qwen2.5-VL"),
    );
    const { result } = renderHook(() => useImageShare({ onReady: vi.fn() }));

    await act(async () => {
      pickFile(new File([new Uint8Array([1])], "x.png", { type: "image/png" }));
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.error).toContain("Qwen2.5-VL"));
  });

  it("enabled=false 时不创建文件选择器（结构上不存在采集）", () => {
    renderHook(() => useImageShare({ onReady: vi.fn(), enabled: false }));
    expect(document.querySelector('input[type="file"]')).toBeNull();
  });
});

// =============================================================
// 语音快捷键（用户在插件设置里自己填）
// =============================================================

/** 向 window 派发一次 keydown（快捷键监听挂在 window 上） */
function pressKey(
  key: string,
  modifiers: { ctrl?: boolean; shift?: boolean; alt?: boolean; meta?: boolean } = {},
  target: EventTarget = window,
) {
  const event = new KeyboardEvent("keydown", {
    key,
    ctrlKey: Boolean(modifiers.ctrl),
    shiftKey: Boolean(modifiers.shift),
    altKey: Boolean(modifiers.alt),
    metaKey: Boolean(modifiers.meta),
    bubbles: true,
    cancelable: true,
  });
  target.dispatchEvent(event);
  return event;
}

describe("useSpeechInput 的快捷键", () => {
  it("按一下开始录音、再按一下结束", async () => {
    mockedTranscribe.mockResolvedValue({
      text: "好的",
      provider: "fake",
      language: "zh",
      duration_seconds: 1,
      warnings: [],
      has_text: true,
    });
    const { result } = renderHook(() =>
      useSpeechInput({ onText: vi.fn(), shortcut: "Ctrl+Shift+M" }),
    );

    await act(async () => {
      pressKey("m", { ctrl: true, shift: true });
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.recording).toBe(true));

    await act(async () => {
      pressKey("m", { ctrl: true, shift: true });
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.recording).toBe(false));
    // 结束时会走转写（与点按钮完全同一条路径）
    expect(mockedTranscribe).toHaveBeenCalledTimes(1);
  });

  it("未配置快捷键时不响应任何按键", async () => {
    const { result } = renderHook(() => useSpeechInput({ onText: vi.fn(), shortcut: "" }));

    await act(async () => {
      pressKey("m", { ctrl: true, shift: true });
      await Promise.resolve();
    });
    expect(result.current.recording).toBe(false);
  });

  it("修饰键不匹配时不触发", async () => {
    const { result } = renderHook(() =>
      useSpeechInput({ onText: vi.fn(), shortcut: "Ctrl+M" }),
    );

    await act(async () => {
      pressKey("m", { ctrl: true, shift: true }); // 多按了 Shift
      await Promise.resolve();
    });
    expect(result.current.recording).toBe(false);
  });

  it("长按连发的 keydown 被忽略（不会「开了又关」）", async () => {
    const { result } = renderHook(() =>
      useSpeechInput({ onText: vi.fn(), shortcut: "Ctrl+M" }),
    );

    await act(async () => {
      const event = new KeyboardEvent("keydown", {
        key: "m",
        ctrlKey: true,
        bubbles: true,
        cancelable: true,
        repeat: true,
      });
      window.dispatchEvent(event);
      await Promise.resolve();
    });
    expect(result.current.recording).toBe(false);
  });

  it("裸键快捷键在输入框里让路（用户打字打到 M 不该开始录音）", async () => {
    const { result } = renderHook(() =>
      useSpeechInput({ onText: vi.fn(), shortcut: "M" }),
    );

    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);

    await act(async () => {
      pressKey("m", {}, textarea);
      await Promise.resolve();
    });
    expect(result.current.recording).toBe(false);

    // 但焦点不在输入框时，裸键照常工作
    await act(async () => {
      pressKey("m", {});
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.recording).toBe(true));
  });

  it("带 Ctrl 的快捷键在输入框里也能触发", async () => {
    const { result } = renderHook(() =>
      useSpeechInput({ onText: vi.fn(), shortcut: "Ctrl+M" }),
    );

    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);

    await act(async () => {
      pressKey("m", { ctrl: true }, textarea);
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.recording).toBe(true));
  });

  it("enabled=false 时快捷键也不响应（后端没启用就别开麦克风）", async () => {
    const { result } = renderHook(() =>
      useSpeechInput({ onText: vi.fn(), enabled: false, shortcut: "Ctrl+M" }),
    );

    await act(async () => {
      pressKey("m", { ctrl: true });
      await Promise.resolve();
    });
    expect(result.current.recording).toBe(false);
  });

  it("shortcutLabel 供界面展示（未配置时为空）", () => {
    const withShortcut = renderHook(() =>
      useSpeechInput({ onText: vi.fn(), shortcut: "ctrl+shift+m" }),
    );
    expect(withShortcut.result.current.shortcutLabel).toBe("Ctrl+Shift+M");

    const without = renderHook(() => useSpeechInput({ onText: vi.fn() }));
    expect(without.result.current.shortcutLabel).toBe("");
  });
});
