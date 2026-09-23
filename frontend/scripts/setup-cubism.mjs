#!/usr/bin/env node
/**
 * 把官方 **Cubism SDK for Web** 落位到本项目。
 *
 * 为什么要有这个脚本：
 * Cubism Core / Framework / Shaders 都**不能**入库（专有许可，且体积大），
 * 而它们必须待在两个固定位置才能被构建系统找到：
 *   - `vendor/cubism/`        → 参与 Next.js/webpack 编译（Framework 的 TS 源码）
 *   - `public/vendor/cubism/` → 运行时可访问（Core 的 min.js、Shaders 的 fgl/vert）
 * 这个脚本就是把「从官网下载的 SDK 解压包」翻译成上面两个位置。
 *
 * 用法：
 *   node scripts/setup-cubism.mjs <SDK目录>        # SDK目录 = 解压后的 CubismSdkForWeb-*
 *   node scripts/setup-cubism.mjs <SDK目录> --force # 允许覆盖已存在的落位
 *   node scripts/setup-cubism.mjs                  # 在当前目录下自动探测 SDK
 *
 * 下载：https://www.live2d.com/en/sdk/download/web/ （需同意官方许可）
 *
 * 本脚本零依赖（只用 Node 内置模块），可重复执行。
 */

import { existsSync, mkdirSync, readdirSync, rmSync, copyFileSync, readFileSync, writeFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(scriptDir, "..");
const repoDir = path.resolve(frontendDir, "..");

const VENDOR_DIR = path.join(frontendDir, "vendor", "cubism");
const PUBLIC_CUBISM_DIR = path.join(frontendDir, "public", "vendor", "cubism");
const BRIDGE_TEMPLATE = path.join(scriptDir, "templates", "cubism-bridge.ts.tmpl");
const BRIDGE_TARGET = path.join(VENDOR_DIR, "bridge.ts");

/** SDK 内必须存在的相对路径（缺任何一项说明目录选错了） */
const REQUIRED = [
  "Core/live2dcubismcore.min.js",
  "Framework/src/live2dcubismframework.ts",
  "Framework/src/model/cubismusermodel.ts",
  "Framework/Shaders/WebGL",
];

const args = process.argv.slice(2);
const force = args.includes("--force");
const inputDir = args.find((arg) => !arg.startsWith("--"));

function log(message) {
  process.stdout.write(`${message}\n`);
}

function fail(message) {
  process.stderr.write(`\n[FAIL] ${message}\n`);
  process.exit(1);
}

/** 深度受限地寻找包含 Core/ 与 Framework/ 的 SDK 根目录 */
function findSdkRoot(start, depth = 0) {
  if (depth > 3 || !existsSync(start)) return null;
  if (REQUIRED.every((rel) => existsSync(path.join(start, rel)))) return start;
  let entries = [];
  try {
    entries = readdirSync(start, { withFileTypes: true });
  } catch {
    return null;
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const found = findSdkRoot(path.join(start, entry.name), depth + 1);
    if (found) return found;
  }
  return null;
}

/** 递归复制目录（保持相对结构） */
function copyTree(fromDir, toDir) {
  mkdirSync(toDir, { recursive: true });
  let count = 0;
  for (const entry of readdirSync(fromDir, { withFileTypes: true })) {
    const from = path.join(fromDir, entry.name);
    const to = path.join(toDir, entry.name);
    if (entry.isDirectory()) {
      count += copyTree(from, to);
      continue;
    }
    copyFileSync(from, to);
    count += 1;
  }
  return count;
}

/**
 * 给官方 Framework 源码顶部注入 `// @ts-nocheck`（幂等）。
 *
 * 原因：官方 Framework 的 tsconfig 只开了 `noImplicitAny`，并未开启 `strict`；
 * 本项目是 `strict: true`，直接编译这些源码会报上百条「null 不能赋值」类错误。
 * 加注释只跳过这些文件**内部**的错误，不影响它们导出的类型。
 */
function injectTsNoCheck(dir) {
  let changed = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      changed += injectTsNoCheck(full);
      continue;
    }
    if (!entry.name.endsWith(".ts")) continue;
    const source = readFileSync(full, "utf8");
    if (source.startsWith("// @ts-nocheck")) continue;
    const header = [
      "// @ts-nocheck",
      "// 由 scripts/setup-cubism.mjs 注入：官方 Framework 面向非 strict 配置编译，",
      "// 在本项目的 strict:true 下会产生大量「null 未初始化」类误报。",
      "// 这些文件导出的类型仍然有效，仅跳过其内部检查。",
      "",
    ].join("\n");
    writeFileSync(full, header + source, "utf8");
    changed += 1;
  }
  return changed;
}

// ---------------------------------------------------------------
// 1. 定位 SDK
// ---------------------------------------------------------------
const searchRoot = inputDir ? path.resolve(process.cwd(), inputDir) : repoDir;
if (inputDir && !existsSync(searchRoot)) fail(`目录不存在：${searchRoot}`);

const sdkRoot = findSdkRoot(searchRoot);
if (!sdkRoot) {
  fail(
    [
      `在 ${searchRoot} 下没找到 Cubism SDK（缺 Core/ 或 Framework/）。`,
      "",
      "请从 https://www.live2d.com/en/sdk/download/web/ 下载 Cubism SDK for Web，解压后把解压目录（或它的上级目录）作为参数传入：",
      "  node scripts/setup-cubism.mjs /path/to/CubismSdkForWeb-5-r.x",
    ].join("\n"),
  );
}

const versionHint = path.basename(sdkRoot);
log(`[OK]   找到 SDK：${sdkRoot}`);

// ---------------------------------------------------------------
// 2. 落位（vendor/ 参与编译，public/ 运行时访问）
// ---------------------------------------------------------------
if (existsSync(VENDOR_DIR)) {
  if (!force) {
    log(`[WARN] ${path.relative(repoDir, VENDOR_DIR)} 已存在，增量覆盖（加 --force 可先清空重来）`);
  } else {
    rmSync(VENDOR_DIR, { recursive: true, force: true });
    log(`[OK]   已清空旧落位（--force）`);
  }
}

const frameworkFiles = copyTree(path.join(sdkRoot, "Framework", "src"), path.join(VENDOR_DIR, "Framework", "src"));
log(`[OK]   Framework 源码 → ${path.relative(repoDir, path.join(VENDOR_DIR, "Framework", "src"))}（${frameworkFiles} 个文件）`);

const shaderFiles = copyTree(
  path.join(sdkRoot, "Framework", "Shaders", "WebGL"),
  path.join(PUBLIC_CUBISM_DIR, "Shaders", "WebGL"),
);
log(`[OK]   Shaders → ${path.relative(repoDir, path.join(PUBLIC_CUBISM_DIR, "Shaders", "WebGL"))}（${shaderFiles} 个文件）`);

mkdirSync(path.join(PUBLIC_CUBISM_DIR, "Core"), { recursive: true });
// Core 的 js 给运行时（<script> 注入）；d.ts 给类型检查（放在源码树里，
// 这样 tsconfig 可以把 public/ 整个排除，避免静态资源被 tsc 扫描）。
const coreJs = path.join(sdkRoot, "Core", "live2dcubismcore.min.js");
if (existsSync(coreJs)) {
  copyFileSync(coreJs, path.join(PUBLIC_CUBISM_DIR, "Core", "live2dcubismcore.min.js"));
  log(`[OK]   Core 运行时 → ${path.relative(repoDir, path.join(PUBLIC_CUBISM_DIR, "Core", "live2dcubismcore.min.js"))}`);
}
const coreDts = path.join(sdkRoot, "Core", "live2dcubismcore.d.ts");
if (existsSync(coreDts)) {
  const dtsDir = path.join(VENDOR_DIR, "Core");
  mkdirSync(dtsDir, { recursive: true });
  copyFileSync(coreDts, path.join(dtsDir, "live2dcubismcore.d.ts"));
  log(`[OK]   Core 类型 → ${path.relative(repoDir, path.join(dtsDir, "live2dcubismcore.d.ts"))}`);
}

// ---------------------------------------------------------------
// 2.5 给官方 Framework 源码加 @ts-nocheck
// ---------------------------------------------------------------
// 官方 Framework 自己的 tsconfig 只开了 noImplicitAny（没开 strict），
// 在本项目的 strict: true 下会产生上百条"null 不能赋值给 X"类误报。
// 加 @ts-nocheck 只跳过这些文件**内部**的错误报告，
// 它们导出的类型仍然有效（bridge.ts 照常获得类型检查）。
const nocheckAdded = injectTsNoCheck(path.join(VENDOR_DIR, "Framework", "src"));
log(`[OK]   Framework 已加 @ts-nocheck：${nocheckAdded} 个文件（尊重官方的编译设置）`);

// ---------------------------------------------------------------
// 3. 生成运行时桥（vendor/cubism/bridge.ts）
// ---------------------------------------------------------------
if (!existsSync(BRIDGE_TEMPLATE)) fail(`缺少模板：${BRIDGE_TEMPLATE}`);
const bridgeSource = readFileSync(BRIDGE_TEMPLATE, "utf8");
const header = [
  "// ⚠️ 本文件由 scripts/setup-cubism.mjs 从 templates/cubism-bridge.ts.tmpl 自动生成。",
  `// 来源 SDK：${versionHint}`,
  "// 生成时间：" + new Date().toISOString(),
  "// 该目录（frontend/vendor/cubism/）已被 .gitignore 排除，请勿手工提交。",
  "",
].join("\n");
mkdirSync(VENDOR_DIR, { recursive: true });
writeFileSync(BRIDGE_TARGET, header + bridgeSource, "utf8");
log(`[OK]   运行时桥 → ${path.relative(repoDir, BRIDGE_TARGET)}`);

// ---------------------------------------------------------------
// 4. 提示后续步骤
// ---------------------------------------------------------------
log(
  [
    "",
    "[DONE] Cubism SDK 落位完成。",
    "",
    "接下来还需要一个模型（授权允许你使用即可，模型不入库）：",
    "  1. 把模型目录整体放到 frontend/public/live2d/<模型id>/",
    "  2. 入口文件命名为 pet.model3.json（在 model3.json 里引用到的 moc3/贴图/物理/表情/动作",
    "     必须保持相对路径不变）",
    "  3. 如需更换默认模型 id，改 frontend/lib/live2d/model-assets.ts",
    "  4. 模型参数名与表情名不同的话，改这两个文件（都有注释）：",
    "     - frontend/lib/live2d/parameter-map.json",
    "     - frontend/lib/live2d/presets.json",
    "",
    "验证：cd frontend && npm run dev → 数字人区域应显示 Live2D 模型",
    "（未安装 SDK 时自动降级为静态立绘，页面会给出明确提示。）",
    "",
  ].join("\n"),
);
