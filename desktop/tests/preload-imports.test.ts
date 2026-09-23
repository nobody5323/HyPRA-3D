import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const preloadDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "../src/preload");

/**
 * 收集源码里**运行时**会用到的模块路径。
 *
 * 先去掉注释再扫描：注释里可能出现示例代码（本文件就有），不能当成真依赖；
 * `import type` 编译后会被完全擦除，也不计入。
 */
function collectRuntimeImportSources(source: string): string[] {
  const withoutComments = source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/.*$/gm, "");
  const withoutTypeImports = withoutComments.replace(
    /import\s+type\s[\s\S]*?from\s+["'][^"']+["'];?/g,
    "",
  );
  const matches = withoutTypeImports.matchAll(
    /from\s+["']([^"']+)["']|import\s+["']([^"']+)["']/g,
  );

  return [...matches].map((match) => match[1] ?? match[2]).filter(Boolean);
}

describe("preload 的模块依赖边界", () => {
  for (const fileName of fs.readdirSync(preloadDir)) {
    it(`${fileName} 只依赖 electron（sandbox 限制）`, () => {
      const source = fs.readFileSync(path.join(preloadDir, fileName), "utf8");

      // preload 运行在 sandbox 里，require 只认 electron 与极少数内置模块；
      // 一旦 import 了本地文件，运行时会直接抛错，症状是 window.hyprPet 根本没注入。
      // 「通道名必须内联」这条约束就靠本用例守着。
      expect(collectRuntimeImportSources(source)).toEqual(["electron"]);
    });
  }
});
