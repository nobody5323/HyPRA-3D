/**
 * 口型时间轴处理测试。
 *
 * 这里锁的是「后端 viseme → 渲染参数」这层翻译的正确性：
 * 后端 10 种口型必须都有映射（漏一种就会静默变成闭口），
 * 时间轴之外的时刻必须闭嘴（绝不能让嘴停在张开状态）。
 */

import { describe, expect, it } from "vitest";

import {
  CLOSED_MOUTH,
  SILENT_VISEME,
  VISEME_MOUTH_FORM,
  VISEME_OPENNESS,
  charOffsetToMs,
  normalizeViseme,
  sampleMouth,
  timelineDurationMs,
} from "@/lib/avatar/lipsync";
import type { VisemeFrame } from "@/lib/api/types";

/** 后端 `Viseme` 枚举的全部取值（backend/app/digital_human/models.py） */
const BACKEND_VISEMES = ["sil", "A", "I", "U", "E", "O", "M", "F", "N", "S"];

function frame(start: number, end: number, viseme: string): VisemeFrame {
  return { start_ms: start, end_ms: end, viseme, char: "" };
}

describe("viseme 映射表", () => {
  it("后端 10 种口型都有开口度与嘴形（漏一种会静默变闭口）", () => {
    for (const viseme of BACKEND_VISEMES) {
      expect(VISEME_OPENNESS[viseme], `缺少开口度：${viseme}`).toBeTypeOf("number");
      expect(VISEME_MOUTH_FORM[viseme], `缺少嘴形：${viseme}`).toBeTypeOf("number");
    }
  });

  it("映射表没有多余项（与后端集合一致）", () => {
    expect(Object.keys(VISEME_OPENNESS).sort()).toEqual([...BACKEND_VISEMES].sort());
    expect(Object.keys(VISEME_MOUTH_FORM).sort()).toEqual([...BACKEND_VISEMES].sort());
  });

  it("元音开口度递减：A > O > E > I > U", () => {
    const { A, O, E, I, U } = VISEME_OPENNESS;
    expect(A).toBeGreaterThan(O);
    expect(O).toBeGreaterThan(E);
    expect(E).toBeGreaterThan(I);
    expect(I).toBeGreaterThan(U);
  });

  it("闭唇音与静默不张嘴", () => {
    expect(VISEME_OPENNESS.sil).toBe(0);
    expect(VISEME_OPENNESS.M).toBe(0);
  });

  it("圆唇与扁口的嘴形方向相反", () => {
    expect(VISEME_MOUTH_FORM.O).toBeGreaterThan(0);
    expect(VISEME_MOUTH_FORM.U).toBeGreaterThan(0);
    expect(VISEME_MOUTH_FORM.I).toBeLessThan(0);
    expect(VISEME_MOUTH_FORM.S).toBeLessThan(0);
  });
});

describe("normalizeViseme", () => {
  it("合法值原样返回", () => {
    for (const viseme of BACKEND_VISEMES) expect(normalizeViseme(viseme)).toBe(viseme);
  });

  it("未知值一律按静默处理（不臆造口型）", () => {
    expect(normalizeViseme("X")).toBe(SILENT_VISEME);
    expect(normalizeViseme("")).toBe(SILENT_VISEME);
    expect(normalizeViseme(null)).toBe(SILENT_VISEME);
    expect(normalizeViseme(undefined)).toBe(SILENT_VISEME);
    expect(normalizeViseme(123)).toBe(SILENT_VISEME);
    expect(normalizeViseme("constructor")).toBe(SILENT_VISEME);
  });
});

describe("timelineDurationMs", () => {
  it("取最后一帧的结束时间", () => {
    expect(timelineDurationMs([frame(0, 100, "A"), frame(100, 350, "O")])).toBe(350);
  });

  it("空时间轴为 0", () => {
    expect(timelineDurationMs([])).toBe(0);
  });
});

describe("sampleMouth", () => {
  const timeline = [frame(0, 100, "A"), frame(100, 200, "I"), frame(300, 400, "O")];

  it("命中帧时返回该口型的映射值", () => {
    expect(sampleMouth(timeline, 50)).toEqual({
      openness: VISEME_OPENNESS.A,
      form: VISEME_MOUTH_FORM.A,
    });
    expect(sampleMouth(timeline, 150).form).toBe(VISEME_MOUTH_FORM.I);
    expect(sampleMouth(timeline, 350).openness).toBe(VISEME_OPENNESS.O);
  });

  it("边界：帧区间是左闭右开", () => {
    expect(sampleMouth(timeline, 0).openness).toBe(VISEME_OPENNESS.A);
    // 100ms 属于第二帧（I），不属于第一帧
    expect(sampleMouth(timeline, 100).form).toBe(VISEME_MOUTH_FORM.I);
  });

  it("落在帧间空隙时闭嘴（后端用空隙表示停顿）", () => {
    expect(sampleMouth(timeline, 250)).toEqual(CLOSED_MOUTH);
  });

  it("时间轴播完后闭嘴（不能停在张开状态）", () => {
    expect(sampleMouth(timeline, 10_000)).toEqual(CLOSED_MOUTH);
  });

  it("负时间（时钟抖动）闭嘴", () => {
    expect(sampleMouth(timeline, -1)).toEqual(CLOSED_MOUTH);
  });

  it("空时间轴或空帧闭嘴", () => {
    expect(sampleMouth([], 50)).toEqual(CLOSED_MOUTH);
    expect(sampleMouth([frame(0, 100, "unknown")], 50)).toEqual(CLOSED_MOUTH);
  });
});

/**
 * 字符位置 → 播放时间。
 *
 * 服务端 TTS 是整段合成：一次请求得到整段音频，但字幕仍要按段推进，
 * 因此需要把「某段首字在全文里的位置」换算成时间点。
 */
describe("charOffsetToMs：字符位置 → 播放时间", () => {
  const perChar = (text: string) =>
    Array.from(text).map((char, index) => frame(index * 200, (index + 1) * 200, "A"));

  it("每帧对应一个字符时精确取该帧起点", () => {
    const timeline = perChar("我在。慢慢说。");
    expect(charOffsetToMs(timeline, 0, 7, 4_000)).toBe(0);
    expect(charOffsetToMs(timeline, 3, 7, 4_000)).toBe(600);
    expect(charOffsetToMs(timeline, 6, 7, 4_000)).toBe(1_200);
  });

  it("帧数与文本长度不符时按时长比例估算（含超出文本长度）", () => {
    const timeline = perChar("我在。"); // 3 帧，但文本 6 字
    expect(charOffsetToMs(timeline, 3, 6, 2_000)).toBe(1_000);
    // 越界的偏移量不得算出越界的时间
    expect(charOffsetToMs(timeline, 99, 6, 2_000)).toBe(2_000);
  });

  it("没有时间轴 / 时长为 0 时返回 0（不得超过音频末尾）", () => {
    expect(charOffsetToMs(null, 3, 6, 0)).toBe(0);
    expect(charOffsetToMs(undefined, 3, 6, 2_000)).toBe(1_000);
  });

  it("空文本返回 0（不能除零）", () => {
    expect(charOffsetToMs([], 0, 0, 2_000)).toBe(0);
  });

  it("负数偏移量按 0 处理", () => {
    expect(charOffsetToMs(perChar("我在。"), -5, 3, 600)).toBe(0);
  });
});
