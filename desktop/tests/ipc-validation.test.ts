import { describe, expect, it } from "vitest";

import { requireBoolean, requirePoint, requireSettingsPatch } from "../src/main/ipcValidation";

describe("requireBoolean", () => {
  it("接受布尔值", () => {
    expect(requireBoolean(true, "value")).toBe(true);
    expect(requireBoolean(false, "value")).toBe(false);
  });

  it("拒绝非布尔值（含真值字符串）", () => {
    expect(() => requireBoolean("true", "value")).toThrow(TypeError);
    expect(() => requireBoolean(1, "value")).toThrow(TypeError);
    expect(() => requireBoolean(undefined, "value")).toThrow(TypeError);
  });
});

describe("requirePoint", () => {
  it("接受有限数值坐标", () => {
    expect(requirePoint({ x: 10, y: -20 })).toEqual({ x: 10, y: -20 });
  });

  it("拒绝非对象、缺字段与非有限数值", () => {
    expect(() => requirePoint(null)).toThrow(TypeError);
    expect(() => requirePoint("10,20")).toThrow(TypeError);
    expect(() => requirePoint({ x: 10 })).toThrow(TypeError);
    expect(() => requirePoint({ x: Number.NaN, y: 0 })).toThrow(TypeError);
    expect(() => requirePoint({ x: 0, y: Number.POSITIVE_INFINITY })).toThrow(TypeError);
    expect(() => requirePoint({ x: "10", y: 20 })).toThrow(TypeError);
  });

  it("只返回白名单字段（不把多余字段透传下去）", () => {
    expect(requirePoint({ x: 1, y: 2, injected: true })).toEqual({ x: 1, y: 2 });
  });
});

describe("requireSettingsPatch", () => {
  it("只挑出已知字段，其余一概丢弃", () => {
    // 设置文件会被下次启动读回，不能让渲染层借 patch 往里塞任意内容
    expect(requireSettingsPatch({ scale: 1.2, evil: "payload" })).toEqual({ scale: 1.2 });
    expect(requireSettingsPatch({ alwaysOnTop: false })).toEqual({ alwaysOnTop: false });
  });

  it("缩放会被收敛到合法区间并保留两位小数", () => {
    expect(requireSettingsPatch({ scale: 99 }).scale).toBe(1.5);
    expect(requireSettingsPatch({ scale: 1.0500000000000003 }).scale).toBe(1.05);
  });

  it("开关项不是布尔时报错（宁可拒绝，也不静默写入错值）", () => {
    expect(() => requireSettingsPatch({ alwaysOnTop: "yes" })).toThrow(TypeError);
  });

  it("非对象入参报错", () => {
    expect(() => requireSettingsPatch(null)).toThrow(TypeError);
    expect(() => requireSettingsPatch("scale=1")).toThrow(TypeError);
  });
});
