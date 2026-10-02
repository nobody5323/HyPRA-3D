import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { buildContentSecurityPolicy } from "../src/main/assetServer";

/**
 * 桌宠窗有**两处** CSP：
 * 1. 页面里的 `<meta http-equiv>`（`pet.html`）；
 * 2. 主进程以响应头下发的（`assetServer.buildContentSecurityPolicy`）。
 *
 * 浏览器同时收到两者时取**交集**，所以「放行某个外部来源」必须**两处都改**
 * ——只改一处会被另一处静默挡掉，现场表现就是「魔珐 SDK 脚本加载失败」，
 * 而 CSP 拦截在渲染层不会有明显报错，排查起来毫无线索。
 *
 * 这组测试把两处钉在一起：任何一边漏掉魔珐域名，这里都会先炸。
 */

const PET_HTML_PATH = fileURLToPath(new URL("../pet.html", import.meta.url));

/** 从页面里抠出 meta CSP 的 content（属性之间有换行，用 \s+ 而不是空格） */
function extractMetaCsp(html: string): string {
  const match = html.match(/http-equiv="Content-Security-Policy"\s+content="([^"]+)"/);

  if (!match) {
    throw new Error("pet.html 里找不到 CSP meta 标签");
  }

  return match[1];
}

/** 取某条指令（CSP 用 `; ` 分隔） */
function directive(csp: string, name: string): string | undefined {
  return csp.split("; ").find((part) => part.startsWith(`${name} `) || part === name);
}

describe("桌宠页面的 CSP（pet.html 的 meta 标签）", () => {
  const csp = extractMetaCsp(readFileSync(PET_HTML_PATH, "utf8"));

  it("放行了魔珐数字人的脚本、连接域名空间与资源 CDN", () => {
    expect(csp).toContain("https://media.xingyun3d.com");
    // 连接类用**厂商级通配**：SDK 会连一串子域，且官方文档没写全——
    // 真正跑驱动的 WebSocket 在 ttsa-gateway-lite.xingyun3d.com
    expect(csp).toContain("https://*.xingyun3d.com");
    expect(csp).toContain("wss://*.xingyun3d.com");
    // 形象数据在阿里云 OSS 且是 fetch 拉的（必须在 connect-src）；**不给通配**
    expect(csp).toContain("https://public-xmov.oss-cn-hangzhou.aliyuncs.com");
  });

  it("外部脚本源是精确白名单，不是放开 https 通配", () => {
    // 锁死确切内容：往后谁想往里加通配符或 'unsafe-eval'，这条会先炸
    expect(directive(csp, "script-src")).toBe(
      "script-src 'self' https://media.xingyun3d.com",
    );
  });

  it("页面自身仍然禁止对象嵌入、禁止被嵌框", () => {
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("frame-src 'none'");
    expect(csp).toContain("base-uri 'none'");
    expect(csp).toContain("form-action 'none'");
  });

  it("本机后端仍在放行名单里（音频与接口不能被顺手挤掉）", () => {
    expect(csp).toContain("http://127.0.0.1:*");
    expect(csp).toContain("http://localhost:*");
  });

  it("放行 blob: Web Worker（魔珐 SDK 依赖它，缺了会报「浏览器能力检查失败」）", () => {
    // 这条是实测出来的：worker-src 未设时会回落到 script-src（其中没有 blob:），
    // Worker 被拦，而 SDK 只报一句能力检查失败——WebGL 与编解码器其实全都正常
    expect(directive(csp, "worker-src")).toBe("worker-src 'self' blob:");
  });
});

describe("两处 CSP 必须一致（改一处漏一处 = SDK 静默失效）", () => {
  const metaCsp = extractMetaCsp(readFileSync(PET_HTML_PATH, "utf8"));
  const headerCsp = buildContentSecurityPolicy("http://localhost:8000");

  const XMOV_ALLOWED = [
    "https://media.xingyun3d.com",
    "https://*.xingyun3d.com",
    "wss://*.xingyun3d.com",
    "https://public-xmov.oss-cn-hangzhou.aliyuncs.com",
  ];

  it.each(XMOV_ALLOWED)("%s 在两处都放行", (origin) => {
    expect(metaCsp).toContain(origin);
    expect(headerCsp).toContain(origin);
  });

  it("两处都不是「放开全部外部脚本」", () => {
    expect(directive(metaCsp, "script-src")).toBe(
      "script-src 'self' https://media.xingyun3d.com",
    );
    expect(directive(headerCsp, "script-src")).toBe(
      "script-src 'self' https://media.xingyun3d.com",
    );
  });

  it("两处都放行了 blob: Worker", () => {
    expect(directive(metaCsp, "worker-src")).toBe("worker-src 'self' blob:");
    expect(directive(headerCsp, "worker-src")).toBe("worker-src 'self' blob:");
  });
});
