import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  buildContentSecurityPolicy,
  injectApiBase,
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

  it("魔珐数字人的脚本、连接域名空间与 Worker 已放行（否则 3D 数字人建不起来）", () => {
    const csp = buildContentSecurityPolicy("http://localhost:8000");

    // 脚本来源**保持精确**（外部脚本越窄越好）
    expect(csp).toContain("https://media.xingyun3d.com");
    // 连接类用**厂商级通配**：实测 SDK 会连一串子域，其中驱动 WebSocket 在
    // ttsa-gateway-lite.xingyun3d.com——官方接入文档里根本没写这个域名
    expect(csp).toContain("https://*.xingyun3d.com");
    expect(csp).toContain("wss://*.xingyun3d.com");
    // SDK 从 blob: 创建 Web Worker（缺了只报「浏览器能力检查失败」）
    expect(csp).toContain("worker-src 'self' blob:");
  });

  it("外部脚本源是精确白名单，不是放开 https 通配", () => {
    const csp = buildContentSecurityPolicy("http://localhost:8000");
    const scriptSrc = csp.split("; ").find((part) => part.startsWith("script-src"));

    // 锁死 script-src 的确切内容：往后谁想往里加通配符，这条测试会先炸
    expect(scriptSrc).toBe("script-src 'self' https://media.xingyun3d.com");
  });
});

/**
 * 后端基址注入（打包形态的 Web 端靠它拿到真实后端地址）。
 *
 * 背景：静态产物里的 `NEXT_PUBLIC_API_BASE` 是构建期内联的，用户改不了。
 * 因此在**托管这一层**把地址注入页面 —— 前端 `lib/api/client.ts` 优先读它。
 */
describe("injectApiBase", () => {
  const PAGE = '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body></body></html>';

  it("把地址注入到 head 开标签之后（必须早于任何脚本）", () => {
    const out = injectApiBase(PAGE, "http://127.0.0.1:8000");

    expect(out).toContain("window.__HYPRA_API_BASE__=");
    expect(out).toContain("http://127.0.0.1:8000");

    // 注入点必须在 <head> 之后、其它内容之前，前端模块求值时才读得到
    const headEnd = out.indexOf("<head>") + "<head>".length;
    expect(out.slice(headEnd, headEnd + 8)).toBe("<script>");
  });

  it("未配置后端地址时原样返回，不往页面里塞无意义的值", () => {
    expect(injectApiBase(PAGE, undefined)).toBe(PAGE);
    expect(injectApiBase(PAGE, "")).toBe(PAGE);
  });

  it("重复调用不会叠加脚本（页面可能被多次请求）", () => {
    const once = injectApiBase(PAGE, "http://127.0.0.1:8000");
    const twice = injectApiBase(once, "http://127.0.0.1:8000");

    expect(twice).toBe(once);
    expect(twice.match(/window\.__HYPRA_API_BASE__/g)).toHaveLength(1);
  });

  it("地址里的引号与 </script> 被转义，不能截断脚本标签", () => {
    const out = injectApiBase(PAGE, 'http://x/"><\\/script><script>alert(1)</script>');

    // 危险序列要么被转义、要么不可能原样出现在脚本体内
    expect(out).not.toContain('</script><script>alert(1)');
    // 页面里应当只有注入的那一个脚本标签的开闭
    expect(out.match(/<\/script>/g)).toHaveLength(1);
  });

  it("没有 <head> 的文档也能注入（插在文档最前）", () => {
    const bare = "<html><body>hi</body></html>";
    const out = injectApiBase(bare, "http://127.0.0.1:8000");

    expect(out.startsWith("<script>")).toBe(true);
  });
});
