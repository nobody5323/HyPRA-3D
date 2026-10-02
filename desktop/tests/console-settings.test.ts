import { describe, expect, it } from "vitest";

import {
  DEFAULT_CONSOLE_SETTINGS,
  isLocalFrontendUrl,
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

/**
 * 「以 Web 模式启动」要不要顺手把本机前端拉起来，全看这个判定。
 *
 * 判错的两种后果都很具体：该拉不拉 → 用户点开一张「无法访问此网站」；
 * 不该拉却拉 → 白起一个 Node 服务占着端口和内存。
 */
describe("isLocalFrontendUrl", () => {
  const PORT = 3000;

  it("本机回环 + 默认端口才算「该由我们负责」", () => {
    expect(isLocalFrontendUrl("http://localhost:3000", PORT)).toBe(true);
    expect(isLocalFrontendUrl("http://127.0.0.1:3000", PORT)).toBe(true);
    expect(isLocalFrontendUrl("http://localhost:3000/chat", PORT)).toBe(true);
  });

  it("远程地址不管：那种前端部署在别处，本地起一个与它无关", () => {
    expect(isLocalFrontendUrl("http://192.168.1.5:3000", PORT)).toBe(false);
    expect(isLocalFrontendUrl("https://hypra.example.com", PORT)).toBe(false);
  });

  it("本机但别的端口不管：那是用户自己起的前端，不该被抢", () => {
    expect(isLocalFrontendUrl("http://localhost:4000", PORT)).toBe(false);
    expect(isLocalFrontendUrl("http://127.0.0.1:8000", PORT)).toBe(false);
  });

  it("省略端口时按协议默认值算（https://localhost 指的是 443，不是 3000）", () => {
    expect(isLocalFrontendUrl("http://localhost", PORT)).toBe(false);
    expect(isLocalFrontendUrl("https://localhost", PORT)).toBe(false);
    expect(isLocalFrontendUrl("http://localhost", 80)).toBe(true);
  });

  it("解析不了的一律当「不是」（宁可不开浏览器，也不去瞎拉进程）", () => {
    expect(isLocalFrontendUrl("", PORT)).toBe(false);
    expect(isLocalFrontendUrl("localhost:3000", PORT)).toBe(false);
    expect(isLocalFrontendUrl("file:///C:/x", PORT)).toBe(false);
  });
});
