/**
 * `useSilentAvatar`（未接入 TTS 时的静默控制器）的行为测试。
 *
 * 它替代了原先的浏览器原生 TTS 实现（Web Speech API 已整体移除），
 * 因此这里锁住的是「**没有语音**」这件事本身：
 * 1. 不会去碰 `speechSynthesis`（哪怕环境里有它）；
 * 2. `speak` / `speakChunks` 立即返回，让调用方的状态机能照常收尾；
 * 3. 分段播报仍然推进字幕回调（文字与语音解耦）。
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useSilentAvatar } from "@/hooks/avatar/useAvatar";
import type { SpeechChunk } from "@/hooks/avatar/useAvatar";

/** 环境里**故意**放一个 speechSynthesis：静默实现绝不能调用它 */
const speechSpy = { speak: vi.fn(), cancel: vi.fn() };

beforeEach(() => {
  speechSpy.speak.mockClear();
  speechSpy.cancel.mockClear();
  Object.defineProperty(window, "speechSynthesis", {
    configurable: true,
    value: speechSpy,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function toChunks(...texts: string[]): SpeechChunk[] {
  return texts.map((text) => ({ text, ssml: `<speak>${text}</speak>` }));
}

describe("useSilentAvatar", () => {
  it("provider 是 none，且说明里写明不播报语音", () => {
    const { result } = renderHook(() => useSilentAvatar());

    expect(result.current.provider).toBe("none");
    expect(result.current.state).toBe("idle");
    expect(result.current.detail).toContain("不播报语音");
  });

  it("speak 立即返回，且完全不碰 speechSynthesis", async () => {
    const { result } = renderHook(() => useSilentAvatar());

    await act(async () => {
      await result.current.speak("我在。");
    });

    expect(speechSpy.speak).not.toHaveBeenCalled();
    expect(speechSpy.cancel).not.toHaveBeenCalled();
  });

  it("speakChunks 逐段推进字幕回调（文字不受没有语音影响），并立即结束", async () => {
    const { result } = renderHook(() => useSilentAvatar());
    const seen: number[] = [];

    await act(async () => {
      await result.current.speakChunks(toChunks("第一段。", "第二段。"), (index) => seen.push(index));
    });

    expect(seen).toEqual([0, 1]);
    expect(speechSpy.speak).not.toHaveBeenCalled();
  });

  it("interrupt 把状态打回 idle", () => {
    const { result } = renderHook(() => useSilentAvatar());

    act(() => {
      result.current.setState("speak");
    });
    expect(result.current.state).toBe("speak");

    act(() => {
      result.current.interrupt();
    });
    expect(result.current.state).toBe("idle");
  });
});
