/**
 * `useBrowserAvatar`（浏览器原生 TTS 实现）的行为测试。
 *
 * 重点是**分段播报的循环控制**：
 * 一段正常播完与「用户打断」在状态上都会回到 idle，循环若用 state 判断就会
 * 只播第一段（或反过来，打断后仍继续播）。这里用可控的 speechSynthesis 替身
 * 精确复现两种时序——每个用例都是对应缺陷的回归测试。
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useBrowserAvatar } from "@/hooks/useAvatar";
import type { SpeechChunk } from "@/hooks/useAvatar";

/** SpeechSynthesisUtterance 替身（只保留实现用到的成员）。 */
class FakeUtterance {
  text: string;
  voice: unknown = null;
  rate = 1;
  onend: ((event: unknown) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;

  constructor(text: string) {
    this.text = text;
  }
}

/**
 * 可控的 speechSynthesis 替身。
 *
 * `speak()` **不自动结束**（与真实浏览器一致：要等语音念完），由测试显式调用
 * `finishCurrent()`（念完）或 `cancel()`（被打断）来推进。这是区分
 * 「正常播完」与「被打断」两种时序的关键。
 */
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
    // 真实浏览器会为被取消的语音触发 onend/onerror，两者都会收敛 Promise
    current?.onend?.(new Event("end"));
  }

  getVoices() {
    return [];
  }

  /** 模拟「当前这段念完了」 */
  finishCurrent() {
    const current = this.current;
    this.current = null;
    current?.onend?.(new Event("end"));
  }
}

let speech: FakeSpeechSynthesis;

beforeEach(() => {
  speech = new FakeSpeechSynthesis();
  vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
  Object.defineProperty(window, "speechSynthesis", {
    value: speech,
    configurable: true,
    writable: true,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** 把若干段文本包装成 speakChunks 需要的形状 */
function toChunks(...texts: string[]): SpeechChunk[] {
  return texts.map((text) => ({ text, ssml: `<speak>${text}</speak>` }));
}

describe("useBrowserAvatar 分段播报", () => {
  it("逐段播出并全部播完（每段都在等上一段结束）", async () => {
    const { result } = renderHook(() => useBrowserAvatar());
    const parts = toChunks("第一段。", "第二段。", "第三段。");

    let done: Promise<void> | undefined;
    act(() => {
      done = result.current.speakChunks(parts);
    });
    // 第一段是同步开始的
    expect(speech.spoken).toEqual(["第一段。"]);

    for (let index = 0; index < parts.length; index += 1) {
      expect(speech.spoken).toHaveLength(index + 1);
      await act(async () => {
        speech.finishCurrent(); // 这一段念完 → 循环进入下一段
      });
    }

    await act(async () => {
      await done;
    });
    expect(speech.spoken).toEqual(["第一段。", "第二段。", "第三段。"]);
  });

  it("被打断后剩余分段不再出声（只播了第一段）", async () => {
    const { result } = renderHook(() => useBrowserAvatar());
    const parts = toChunks("第一段。", "第二段。", "第三段。");

    let done: Promise<void> | undefined;
    act(() => {
      done = result.current.speakChunks(parts);
    });
    expect(speech.spoken).toEqual(["第一段。"]);

    // 用户点「打断」：cancel() 会像真实浏览器那样让当前段结束——
    // 若循环缺乏代次守卫，这里就会继续念第二、三段
    await act(async () => {
      result.current.interrupt();
      await done;
    });

    expect(speech.spoken).toEqual(["第一段。"]);
  });

  it("组件卸载后停止后续分段（不会在卸载后继续出声）", async () => {
    const { result, unmount } = renderHook(() => useBrowserAvatar());
    const parts = toChunks("第一段。", "第二段。");

    let done: Promise<void> | undefined;
    act(() => {
      done = result.current.speakChunks(parts);
    });

    await act(async () => {
      unmount(); // cleanup 里 cancel() 会让当前段结束
      await done;
    });

    expect(speech.spoken).toEqual(["第一段。"]);
  });

  it("整段播报会抢占进行中的分段播报（不叠加朗读）", async () => {
    const { result } = renderHook(() => useBrowserAvatar());
    const parts = toChunks("第一段。", "第二段。");

    let chunksDone: Promise<void> | undefined;
    act(() => {
      chunksDone = result.current.speakChunks(parts);
    });

    let wholeDone: Promise<void> | undefined;
    act(() => {
      wholeDone = result.current.speak("整段回复。");
    });

    // 分段循环已被代次守卫作废，落到「整段」这一段
    await act(async () => {
      speech.finishCurrent();
      await Promise.all([chunksDone, wholeDone]);
    });

    expect(speech.spoken).toEqual(["第一段。", "整段回复。"]);
  });
});
