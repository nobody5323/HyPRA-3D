/**
 * 端到端：真实 Web 产物在**真实注入逻辑**下的行为。
 *
 * 前面 `asset-server.test.ts` 里的 injectApiBase 用例用的是合成 HTML；
 * 这里拿**组装出来的真实产物**（`frontend/out-web/index.html`）过一遍，
 * 确认三件事：
 *
 * 1. 注入点确实落在所有 `_next/static` 引用**之前**——前端 `API_BASE` 是模块级
 *    常量，晚于脚本标签就会读到 `undefined`；
 * 2. 产物自带的后端地址（构建期内联的默认值）不会与注入值打架；
 * 3. 注入不会破坏 HTML 结构。
 *
 * 产物缺失时**跳过而非失败**：`out-web/` 是构建产物、不入库，
 * 单测不应该依赖「先跑过组装脚本」。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { injectApiBase } from "../src/main/assetServer";

const OUT_WEB_INDEX = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../frontend/out-web/index.html",
);

const hasBundle = fs.existsSync(OUT_WEB_INDEX);

describe.skipIf(!hasBundle)("真实 Web 产物的后端地址注入", () => {
  const raw = hasBundle ? fs.readFileSync(OUT_WEB_INDEX, "utf8") : "";

  it("产物本身不含注入值（否则会与运行时注入打架）", () => {
    expect(raw).not.toContain("__HYPRA_API_BASE__");
  });

  it("注入点早于所有 _next/static 引用", () => {
    const injected = injectApiBase(raw, "http://127.0.0.1:8000");

    const injectAt = injected.indexOf("__HYPRA_API_BASE__");
    const firstAssetAt = injected.indexOf("_next/static/");

    expect(injectAt).toBeGreaterThan(-1);
    expect(firstAssetAt).toBeGreaterThan(-1);
    // 这是本测试存在的理由：顺序反了，前端就永远拿不到注入地址
    expect(injectAt).toBeLessThan(firstAssetAt);
  });

  it("注入的地址可被解析出来，且 HTML 结构完好", () => {
    const injected = injectApiBase(raw, "http://127.0.0.1:4321");

    // 从脚本片段里取出实际写入的值
    const match = injected.match(/window\.__HYPRA_API_BASE__=("[^"]*")/);

    expect(match).not.toBeNull();
    expect(JSON.parse(match![1])).toBe("http://127.0.0.1:4321");

    // 原有结构一个不少
    expect(injected).toContain("<html");
    expect(injected).toContain("</body>");
    expect(injected).toContain("</html>");
    // 只多出一个 script 开闭对
    expect(injected.match(/<script>/g)?.length ?? 0).toBe(
      (raw.match(/<script>/g)?.length ?? 0) + 1,
    );
  });

  it("不传地址时产物原样返回", () => {
    expect(injectApiBase(raw, undefined)).toBe(raw);
  });
});
