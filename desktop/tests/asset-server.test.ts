import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  buildContentSecurityPolicy,
  resolveAssetPath,
  resolveRootFor,
} from "../src/main/assetServer";

const ROOT = path.join("C:", "app", "dist", "renderer");

/** 用仓库里一定存在的目录模拟「用户自备模型目录」 */
const LIVE2D_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../frontend/public/live2d",
);

describe("resolveAssetPath", () => {
  it("把请求路径解析到根目录内", () => {
    expect(resolveAssetPath(ROOT, "/pet.html")).toBe(path.join(ROOT, "pet.html"));
    expect(resolveAssetPath(ROOT, "/assets/pet-abc.js")).toBe(
      path.join(ROOT, "assets", "pet-abc.js"),
    );
  });

  it("根路径回落到 index.html", () => {
    expect(resolveAssetPath(ROOT, "/")).toBe(path.join(ROOT, "index.html"));
  });

  it("解码百分号编码的路径", () => {
    expect(resolveAssetPath(ROOT, "/live2d/%E8%8B%8F%E6%BE%84/pet.model3.json")).toBe(
      path.join(ROOT, "live2d", "苏澄", "pet.model3.json"),
    );
  });

  it("拦截 .. 逃逸（含编码形式）", () => {
    expect(resolveAssetPath(ROOT, "/../secret.txt")).toBeUndefined();
    expect(resolveAssetPath(ROOT, "/a/../../secret.txt")).toBeUndefined();
    expect(resolveAssetPath(ROOT, "/%2e%2e/secret.txt")).toBeUndefined();
    expect(resolveAssetPath(ROOT, "/..%5c..%5cwindows")).toBeUndefined();
  });

  it("拦截 Windows 盘符与 NUL 字节", () => {
    expect(resolveAssetPath(ROOT, "/C:/Windows/system32/config")).toBeUndefined();
    expect(resolveAssetPath(ROOT, "/pet%00.html")).toBeUndefined();
  });

  it("拦截非法百分号编码", () => {
    expect(resolveAssetPath(ROOT, "/%E0%A4%A")).toBeUndefined();
  });
});

describe("resolveRootFor", () => {
  const options = {
    root: ROOT,
    extraRoots: { "/live2d": LIVE2D_DIR },
  };

  it("命中额外资源根时去掉前缀", () => {
    expect(resolveRootFor(options, "/live2d/default/pet.model3.json")).toEqual({
      root: options.extraRoots["/live2d"],
      pathname: "/default/pet.model3.json",
    });
  });

  it("未命中时用主根与原始路径", () => {
    expect(resolveRootFor(options, "/portraits/happy.svg")).toEqual({
      root: ROOT,
      pathname: "/portraits/happy.svg",
    });
  });

  it("前缀不同名时不误命中（/live2d-extra 不属于 /live2d）", () => {
    expect(resolveRootFor(options, "/live2d-extra/x.png").root).toBe(ROOT);
  });

  it("额外目录不存在时回落到主根（模型没装也不该 404 变成整页失败）", () => {
    const missing = { root: ROOT, extraRoots: { "/live2d": "C:/definitely/not/here" } };

    expect(resolveRootFor(missing, "/live2d/default/pet.model3.json").root).toBe(ROOT);
  });
});

describe("buildContentSecurityPolicy", () => {
  it("默认只允许自身资源", () => {
    const csp = buildContentSecurityPolicy();

    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("script-src 'self'");
    expect(csp).not.toContain("http://localhost:8000");
  });

  it("后端地址会加进 connect-src 与媒体源（否则音频与接口都会被 CSP 拦掉）", () => {
    const csp = buildContentSecurityPolicy("http://localhost:8000");

    expect(csp).toContain("connect-src 'self' http://localhost:8000");
    expect(csp).toContain("media-src 'self' data: blob: http://localhost:8000");
    expect(csp).toContain("img-src 'self' data: blob: http://localhost:8000");
  });

  it("不允许内联脚本、不允许被嵌入", () => {
    const csp = buildContentSecurityPolicy("http://localhost:8000");

    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("frame-src 'none'");
    expect(csp).not.toContain("script-src 'self' 'unsafe-inline'");
  });
});
