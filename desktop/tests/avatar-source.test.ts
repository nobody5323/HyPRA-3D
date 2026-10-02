import { describe, expect, it } from "vitest";

import { resolveLocalRenderer, resolvePetRenderMode } from "../src/renderer/pet/avatar-source";

/**
 * 桌宠形象来源的决策规则。
 *
 * 这是桌宠窗「能不能用 3D、什么时候回退到 Live2D」的唯一开关。
 * 与 Web 端共享大部分规则，只有 `auto` 对**部署配置凭证**的处理刻意不同
 * （横屏应用不能拿去填竖屏容器，会把人物拉变形）。
 */
describe("resolvePetRenderMode", () => {
  it("auto：界面填过凭证就走魔珐", () => {
    expect(
      resolvePetRenderMode({
        renderer: "auto",
        credentialsSource: "user",
        degraded: false,
      }),
    ).toBe("xmov");
  });

  it("auto：**不**自动套用部署配置的凭证（那是给 Web 端的横屏应用）", () => {
    // 套用会把竖屏容器里的人拉变形；想用就在面板里显式选「魔珐星云 SDK」
    expect(
      resolvePetRenderMode({
        renderer: "auto",
        credentialsSource: "env",
        degraded: false,
      }),
    ).toBe("local");
  });

  it("auto：没有凭证时回本地", () => {
    expect(
      resolvePetRenderMode({
        renderer: "auto",
        credentialsSource: "none",
        degraded: false,
      }),
    ).toBe("local");
  });

  it("xmov：强制魔珐，部署配置的凭证也算（用户明确要求了）", () => {
    expect(
      resolvePetRenderMode({
        renderer: "xmov",
        credentialsSource: "env",
        degraded: false,
      }),
    ).toBe("xmov");
    expect(
      resolvePetRenderMode({
        renderer: "xmov",
        credentialsSource: "user",
        degraded: false,
      }),
    ).toBe("xmov");
  });

  it("xmov：没凭证时回本地（不能给一个空舞台）", () => {
    expect(
      resolvePetRenderMode({
        renderer: "xmov",
        credentialsSource: "none",
        degraded: false,
      }),
    ).toBe("local");
  });

  it("local / static：**强制本地**——填了密钥也想看 Live2D 的场景", () => {
    // 旧逻辑只看「有没有凭证」，会让本地渲染器永远没机会跑（Web 端踩过这个坑）
    for (const renderer of ["local", "static"] as const) {
      expect(
        resolvePetRenderMode({ renderer, credentialsSource: "user", degraded: false }),
      ).toBe("local");
    }
  });

  it("降级后回本地：一次失败要退，但不能把 3D 永久钉死", () => {
    // 「永久钉死」由上层负责：降级标记绑定凭证版本，换一版凭证即失效重试
    expect(
      resolvePetRenderMode({
        renderer: "auto",
        credentialsSource: "user",
        degraded: true,
      }),
    ).toBe("local");
    expect(
      resolvePetRenderMode({
        renderer: "xmov",
        credentialsSource: "user",
        degraded: true,
      }),
    ).toBe("local");
  });

  it("强制本地的偏好不受降级标记影响（结果本来就该是本地）", () => {
    expect(
      resolvePetRenderMode({
        renderer: "static",
        credentialsSource: "none",
        degraded: true,
      }),
    ).toBe("local");
  });
});

/**
 * 「用户选什么就用什么」：本地渲染层拿到的偏好。
 *
 * 这条防的是用户报过的现象——选了魔珐却没起来时，系统自作主张去加载 Live2D。
 */
describe("resolveLocalRenderer", () => {
  it("选了魔珐但没起来 → 只给立绘（不自动换成 Live2D）", () => {
    expect(resolveLocalRenderer("xmov", false)).toBe("static");
  });

  it("选了魔珐且已生效 → 原样（此时本地层根本不挂载，值不影响）", () => {
    expect(resolveLocalRenderer("xmov", true)).toBe("xmov");
  });

  it("其余偏好原样透传", () => {
    expect(resolveLocalRenderer("static", false)).toBe("static");
    expect(resolveLocalRenderer("local", false)).toBe("local");
    expect(resolveLocalRenderer("auto", false)).toBe("auto");
  });
});
