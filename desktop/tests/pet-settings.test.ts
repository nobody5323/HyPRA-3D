import { describe, expect, it } from "vitest";

import {
  DEFAULT_PET_SETTINGS,
  MAX_PET_SCALE,
  MIN_PET_SCALE,
  normalizePetSettings,
  roundScale,
} from "../src/shared/ipc";

describe("normalizePetSettings", () => {
  it("合法值原样保留", () => {
    expect(normalizePetSettings({ scale: 1.25, alwaysOnTop: false, clickThrough: true })).toEqual({
      scale: 1.25,
      alwaysOnTop: false,
      clickThrough: true,
    });
  });

  it("缺字段时回落默认值", () => {
    expect(normalizePetSettings(undefined)).toEqual(DEFAULT_PET_SETTINGS);
    expect(normalizePetSettings({})).toEqual(DEFAULT_PET_SETTINGS);
    expect(normalizePetSettings("not-json")).toEqual(DEFAULT_PET_SETTINGS);
  });

  it("缩放超出区间时贴到边界", () => {
    expect(normalizePetSettings({ scale: 9 }).scale).toBe(MAX_PET_SCALE);
    expect(normalizePetSettings({ scale: 0.1 }).scale).toBe(MIN_PET_SCALE);
  });

  it("缩放对齐到步进（避免 1.0500000000000003 这类值写进设置文件）", () => {
    expect(normalizePetSettings({ scale: 1.0500000000000003 }).scale).toBe(1.05);
    expect(normalizePetSettings({ scale: 0.83 }).scale).toBe(0.85);
  });

  it("非法缩放（NaN / 字符串）回落默认值", () => {
    expect(normalizePetSettings({ scale: Number.NaN }).scale).toBe(DEFAULT_PET_SETTINGS.scale);
    expect(normalizePetSettings({ scale: "1.2" }).scale).toBe(1.2); // 数字字符串可接受
    expect(normalizePetSettings({ scale: "big" }).scale).toBe(DEFAULT_PET_SETTINGS.scale);
  });

  it("非布尔的开关回落默认值", () => {
    expect(normalizePetSettings({ alwaysOnTop: "yes" }).alwaysOnTop).toBe(
      DEFAULT_PET_SETTINGS.alwaysOnTop,
    );
    expect(normalizePetSettings({ clickThrough: 1 }).clickThrough).toBe(
      DEFAULT_PET_SETTINGS.clickThrough,
    );
  });
});

describe("roundScale", () => {
  it("对齐到 0.05 的整数倍", () => {
    expect(roundScale(1.02)).toBe(1);
    expect(roundScale(1.03)).toBe(1.05);
    expect(roundScale(0.7)).toBe(0.7);
  });
});
