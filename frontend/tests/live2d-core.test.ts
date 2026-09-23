/**
 * Cubism Core 加载器测试。
 *
 * Core 是 UMD，必须由页面注入 `<script>`；这里锁住三条约束：
 * 幂等（React 严格模式会挂两次）、失败可重试、以及"脚本能加载但没挂全局"也要算失败。
 */

import { afterEach, describe, expect, it } from "vitest";

import {
  CUBISM_CORE_URL,
  isCubismCoreReady,
  loadCubismCore,
  resetCubismCoreLoader,
} from "@/lib/live2d/cubism-core";

function coreScripts(): NodeListOf<HTMLScriptElement> {
  return document.querySelectorAll<HTMLScriptElement>(`script[src="${CUBISM_CORE_URL}"]`);
}

afterEach(() => {
  Reflect.deleteProperty(globalThis, "Live2DCubismCore");
  resetCubismCoreLoader();
  for (const script of Array.from(coreScripts())) script.remove();
});

describe("isCubismCoreReady", () => {
  it("只有全局对象存在才算就绪", () => {
    expect(isCubismCoreReady()).toBe(false);
    (globalThis as Record<string, unknown>).Live2DCubismCore = {};
    expect(isCubismCoreReady()).toBe(true);
  });
});

describe("loadCubismCore", () => {
  it("已就绪时立即完成，不注入 script", async () => {
    (globalThis as Record<string, unknown>).Live2DCubismCore = {};
    const before = document.scripts.length;

    await loadCubismCore();

    expect(document.scripts.length).toBe(before);
    expect(coreScripts()).toHaveLength(0);
  });

  it("未就绪时注入指向落位路径的 script", () => {
    // 不会真的加载（jsdom 不取资源），因此主动吞掉后续超时拒绝
    void loadCubismCore({ timeoutMs: 1_000 }).catch(() => {});

    const script = coreScripts()[0];
    expect(script).toBeDefined();
    expect(script.src.endsWith(CUBISM_CORE_URL)).toBe(true);
    expect(script.async).toBe(true);
  });

  it("并发调用共享同一次加载，不重复注入", () => {
    void loadCubismCore({ timeoutMs: 1_000 }).catch(() => {});
    void loadCubismCore({ timeoutMs: 1_000 }).catch(() => {});

    expect(coreScripts()).toHaveLength(1);
  });

  it("拿不到资源时按超时失败，并移除坏脚本以便重试", async () => {
    await expect(loadCubismCore({ timeoutMs: 5 })).rejects.toThrow(/超时/);
    expect(coreScripts()).toHaveLength(0);

    // 清空缓存后可再次尝试（会重新注入）
    void loadCubismCore({ timeoutMs: 1_000 }).catch(() => {});
    expect(coreScripts()).toHaveLength(1);
  });

  it("脚本加载成功但没挂上全局，也算失败（放错文件的情况）", async () => {
    const promise = loadCubismCore({ timeoutMs: 1_000 });
    // 手动触发 onload：模拟"文件是别的 JS，或路径指到了空文件"
    coreScripts()[0].onload?.(new Event("load"));

    await expect(promise).rejects.toThrow(/没有导出 Live2DCubismCore/);
  });
});
