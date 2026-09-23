import { describe, expect, it } from "vitest";

import {
  DEFAULT_CONSOLE_SETTINGS,
  normalizeConsoleSettings,
  normalizeWebUrl,
} from "../src/shared/ipc";

/**
 * 控制台设置归一化测试。
 *
 * 重点是 `normalizeWebUrl`：它的输出最终会交给主进程的 `shell.openExternal`，
 * 所以「什么能过、什么必须挡」是这个文件里最要紧的事。
 */
describe("normalizeWebUrl", () => {
  it("接受 http/https，并去掉末尾斜杠", () => {
    expect(normalizeWebUrl("http://localhost:3000")).toBe("http://localhost:3000");
    expect(normalizeWebUrl("http://localhost:3000/")).toBe("http://localhost:3000");
    expect(normalizeWebUrl("https://example.com/app/")).toBe("https://example.com/app");
    expect(normalizeWebUrl("  http://127.0.0.1:3000  ")).toBe("http://127.0.0.1:3000");
  });

  it("拒绝非 http/https 协议（这些都能借 openExternal 拉起本机程序）", () => {
    expect(normalizeWebUrl("file:///C:/Windows/System32/calc.exe")).toBe("");
    expect(normalizeWebUrl("javascript:alert(1)")).toBe("");
    expect(normalizeWebUrl("ms-settings:")).toBe("");
    expect(normalizeWebUrl("ftp://example.com")).toBe("");
  });

  it("拒绝空值、缺协议与乱写", () => {
    expect(normalizeWebUrl("")).toBe("");
    expect(normalizeWebUrl("   ")).toBe("");
    expect(normalizeWebUrl("localhost:3000")).toBe("");
    expect(normalizeWebUrl(null)).toBe("");
    expect(normalizeWebUrl(undefined)).toBe("");
  });
});

describe("normalizeConsoleSettings", () => {
  it("非法地址回落默认值（而不是存个空串让「启动 Web」永远失败）", () => {
    expect(normalizeConsoleSettings({ webUrl: "file:///x" })).toEqual(DEFAULT_CONSOLE_SETTINGS);
    expect(normalizeConsoleSettings({ webUrl: "" })).toEqual(DEFAULT_CONSOLE_SETTINGS);
    expect(normalizeConsoleSettings({})).toEqual(DEFAULT_CONSOLE_SETTINGS);
    expect(normalizeConsoleSettings(null)).toEqual(DEFAULT_CONSOLE_SETTINGS);
  });

  it("合法地址归一化后保留", () => {
    expect(normalizeConsoleSettings({ webUrl: "http://192.168.1.5:8080/ui/" })).toEqual({
      webUrl: "http://192.168.1.5:8080/ui",
    });
  });

  it("只保留已知字段（设置文件会被下次启动读回）", () => {
    expect(normalizeConsoleSettings({ webUrl: "http://x.test", evil: 1 })).toEqual({
      webUrl: "http://x.test",
    });
  });
});
