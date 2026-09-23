/**
 * 立绘清单测试。
 *
 * 重点不是"函数能跑"，而是**清单完整性**：8 类情绪必须都有可用条目，
 * 否则某个情绪会静默落到兜底立绘（表现为"她永远一个表情"）。
 */

import { describe, expect, it } from "vitest";

import { PORTRAIT_IDS } from "@/lib/avatar-renderer";
import {
  PORTRAIT_BASE_PATH,
  PORTRAIT_MANIFEST,
  resolveEmotionPortrait,
  resolvePortraitEntry,
} from "@/lib/portrait-assets";

describe("PORTRAIT_MANIFEST", () => {
  it("8 类情绪都有条目，文件名与 id 同名", () => {
    expect(Object.keys(PORTRAIT_MANIFEST).sort()).toEqual([...PORTRAIT_IDS].sort());
    for (const id of PORTRAIT_IDS) {
      const entry = PORTRAIT_MANIFEST[id];
      expect(entry.id).toBe(id);
      expect(entry.url).toBe(`${PORTRAIT_BASE_PATH}/${id}.svg`);
      expect(entry.name.length).toBeGreaterThan(0);
    }
  });
});

describe("resolvePortraitEntry", () => {
  it("合法 id 原样返回", () => {
    expect(resolvePortraitEntry("happy")).toEqual(PORTRAIT_MANIFEST.happy);
    expect(resolvePortraitEntry("anxious")).toEqual(PORTRAIT_MANIFEST.anxious);
  });

  it("未知 id 回落 neutral，调用方无需判空", () => {
    expect(resolvePortraitEntry("ecstatic")).toEqual(PORTRAIT_MANIFEST.neutral);
    expect(resolvePortraitEntry("")).toEqual(PORTRAIT_MANIFEST.neutral);
    expect(resolvePortraitEntry(null)).toEqual(PORTRAIT_MANIFEST.neutral);
    expect(resolvePortraitEntry(undefined)).toEqual(PORTRAIT_MANIFEST.neutral);
  });
});

describe("resolveEmotionPortrait", () => {
  it("优先使用英文 label", () => {
    expect(resolveEmotionPortrait({ label: "sad", label_zh: "开心" })).toEqual(
      PORTRAIT_MANIFEST.sad,
    );
  });

  it("label 缺失或为空时退回 label_zh", () => {
    expect(resolveEmotionPortrait({ label_zh: "焦虑" })).toEqual(PORTRAIT_MANIFEST.anxious);
    expect(resolveEmotionPortrait({ label: "   ", label_zh: "难过" })).toEqual(
      PORTRAIT_MANIFEST.sad,
    );
  });

  it("情绪为空或未知时回落 neutral", () => {
    expect(resolveEmotionPortrait({})).toEqual(PORTRAIT_MANIFEST.neutral);
    expect(resolveEmotionPortrait(null)).toEqual(PORTRAIT_MANIFEST.neutral);
    expect(resolveEmotionPortrait(undefined)).toEqual(PORTRAIT_MANIFEST.neutral);
    expect(resolveEmotionPortrait({ label: "unknown_mood" })).toEqual(PORTRAIT_MANIFEST.neutral);
  });
});
