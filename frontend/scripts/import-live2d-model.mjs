#!/usr/bin/env node
/**
 * 把已解压的 Live2D 模型导入项目。
 *
 * 解决的核心问题：**VTube Studio 导出的模型，`model3.json` 里往往没有
 * Expressions / Motions 引用**（表情与动作由 VTS 自己管理），
 * 但官方 Cubism SDK 只认 `model3.json` 里的引用——结果是"模型能显示，
 * 却一个表情也切不了、一个动作也播不了"。
 * 本脚本在导入时把缺失的引用补全（不覆盖模型自带的引用）。
 *
 * 用法：
 *   node scripts/import-live2d-model.mjs <已解压的模型目录> [--id default] [--force]
 *
 * 说明：
 * - 模型目录请用系统工具（资源管理器 / 7-Zip）解压：zip 内中文文件名
 *   在命令行解压时容易乱码，而模型是**按文件名引用资源**的。
 * - 模型不入库（`public/live2d/` 已在 .gitignore 中）。
 * - 本脚本零依赖，可重复执行。
 */

import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(scriptDir, "..");
const repoDir = path.resolve(frontendDir, "..");

const ENTRY_NAME = "pet.model3.json";
const EXPRESSION_SUFFIX = ".exp3.json";
const MOTION_SUFFIX = ".motion3.json";
const DEFAULT_MODEL_ID = "default";

const args = process.argv.slice(2);
const force = args.includes("--force");
const idFlagIndex = args.indexOf("--id");
const modelId = idFlagIndex >= 0 ? String(args[idFlagIndex + 1] ?? "").trim() : DEFAULT_MODEL_ID;
const sourceArg = args.find(
  (arg, index) => !arg.startsWith("--") && (idFlagIndex < 0 || index !== idFlagIndex + 1),
);

function log(message) {
  process.stdout.write(`${message}\n`);
}

function fail(message) {
  process.stderr.write(`\n[FAIL] ${message}\n`);
  process.exit(1);
}

if (!sourceArg) {
  fail(
    [
      "缺少模型目录参数。",
      "",
      "用法：node scripts/import-live2d-model.mjs <已解压的模型目录> [--id default] [--force]",
      "例：  node scripts/import-live2d-model.mjs ~/Downloads/DS面捕版",
    ].join("\n"),
  );
}

const sourceDir = path.resolve(process.cwd(), sourceArg);
if (!existsSync(sourceDir) || !statSync(sourceDir).isDirectory()) {
  fail(`不是有效目录：${sourceDir}`);
}
if (!modelId) fail("--id 不能为空");

const targetDir = path.join(frontendDir, "public", "live2d", modelId);

/** 递归列出相对路径（用 / 分隔，保持模型引用风格一致） */
function listFiles(root, current = "", out = []) {
  for (const entry of readdirSync(path.join(root, current), { withFileTypes: true })) {
    const relative = current ? `${current}/${entry.name}` : entry.name;
    if (entry.isDirectory()) listFiles(root, relative, out);
    else out.push(relative);
  }
  return out;
}

// ---------------------------------------------------------------
// 1. 定位入口 model3.json 并复制模型
// ---------------------------------------------------------------
const sourceFiles = listFiles(sourceDir);
const model3Files = sourceFiles.filter(
  (file) => file.endsWith(".model3.json") && path.basename(file) !== ENTRY_NAME,
);
if (model3Files.length === 0) fail(`在 ${sourceDir} 下没找到 *.model3.json`);
if (model3Files.length > 1) {
  log(`[WARN] 目录里有多个 model3.json，使用第一个：${model3Files[0]}`);
}
const sourceModel3 = model3Files[0];
log(`[OK]   入口：${sourceModel3}`);

if (existsSync(targetDir)) {
  if (!force) {
    log(`[WARN] 目标目录已存在，将覆盖其中文件（加 --force 可先清空重来）`);
  } else {
    rmSync(targetDir, { recursive: true, force: true });
    log(`[OK]   已清空旧目录（--force）`);
  }
}
mkdirSync(targetDir, { recursive: true });

let copied = 0;
for (const relative of sourceFiles) {
  const from = path.join(sourceDir, relative);
  const to = path.join(targetDir, relative);
  mkdirSync(path.dirname(to), { recursive: true });
  copyFileSync(from, to);
  copied += 1;
}
log(`[OK]   已复制 ${copied} 个文件 → ${path.relative(repoDir, targetDir)}`);

// ---------------------------------------------------------------
// 2. 补全 model3.json 的 Expressions / Motions 引用
// ---------------------------------------------------------------
const model3 = JSON.parse(readFileSync(path.join(sourceDir, sourceModel3), "utf8"));
model3.FileReferences ??= {};

const presentFiles = listFiles(targetDir);
const expressionFiles = presentFiles.filter((file) => file.endsWith(EXPRESSION_SUFFIX));
const motionFiles = presentFiles.filter((file) => file.endsWith(MOTION_SUFFIX));

let addedExpressions = 0;
if (!Array.isArray(model3.FileReferences.Expressions) || model3.FileReferences.Expressions.length === 0) {
  model3.FileReferences.Expressions = expressionFiles
    .map((file) => ({
      Name: path.basename(file, EXPRESSION_SUFFIX),
      File: file,
    }))
    .sort((a, b) => a.Name.localeCompare(b.Name, "zh"));
  addedExpressions = model3.FileReferences.Expressions.length;
}

let addedMotionGroups = [];
if (!model3.FileReferences.Motions || Object.keys(model3.FileReferences.Motions).length === 0) {
  const motions = {};
  for (const file of motionFiles) {
    const base = path.basename(file, MOTION_SUFFIX);
    // 名字里带 idle 的归入官方约定的 Idle 组（桥会把它设为循环待机）
    const group = /idle/i.test(base) ? "Idle" : base;
    motions[group] ??= [];
    motions[group].push({ File: file, FadeInTime: 0.5, FadeOutTime: 0.5 });
  }
  // Idle 排在最前，方便人读
  const ordered = {};
  for (const key of Object.keys(motions).sort((a, b) => (a === "Idle" ? -1 : b === "Idle" ? 1 : a.localeCompare(b, "zh")))) {
    ordered[key] = motions[key];
  }
  model3.FileReferences.Motions = ordered;
  addedMotionGroups = Object.keys(ordered);
}

// 补 LipSync 参数组：官方 SDK 的部分辅助功能会读它（本项目的桥直接用参数名，不依赖）
const mouthParam = "ParamMouthOpenY";
const displayInfoFile = model3.FileReferences.DisplayInfo;
let hasMouthParam = false;
if (displayInfoFile && existsSync(path.join(targetDir, displayInfoFile))) {
  const displayInfo = JSON.parse(readFileSync(path.join(targetDir, displayInfoFile), "utf8"));
  hasMouthParam = (displayInfo.Parameters ?? []).some((param) => param.Id === mouthParam);
} else {
  hasMouthParam = true; // 没有 cdi3 时无从判断，默认按标准名写入
}
model3.Groups ??= [];
if (hasMouthParam) {
  const lipSync = model3.Groups.find((group) => group.Name === "LipSync");
  if (lipSync) {
    if (!Array.isArray(lipSync.Ids) || lipSync.Ids.length === 0) lipSync.Ids = [mouthParam];
  } else {
    model3.Groups.push({ Target: "Parameter", Name: "LipSync", Ids: [mouthParam] });
  }
}

writeFileSync(path.join(targetDir, ENTRY_NAME), `${JSON.stringify(model3, null, 1)}\n`, "utf8");
log(`[OK]   入口已生成：${ENTRY_NAME}`);
if (addedExpressions) log(`[OK]   补全 Expressions：${addedExpressions} 个（原 model3.json 未引用）`);
else log(`[OK]   Expressions 原本已引用 ${model3.FileReferences.Expressions?.length ?? 0} 个，保持不变`);
if (addedMotionGroups.length) log(`[OK]   补全 Motions 组：${addedMotionGroups.join("、")}`);
else log(`[OK]   Motions 原本已存在，保持不变`);

// ---------------------------------------------------------------
// 3. 诊断：参数映射核对 + 清单（供填 presets.json）
// ---------------------------------------------------------------
const parameterMap = JSON.parse(
  readFileSync(path.join(frontendDir, "lib", "live2d", "parameter-map.json"), "utf8"),
);
const displayInfoPath = model3.FileReferences.DisplayInfo
  ? path.join(targetDir, model3.FileReferences.DisplayInfo)
  : null;
const parameterIds = new Set();
if (displayInfoPath && existsSync(displayInfoPath)) {
  const displayInfo = JSON.parse(readFileSync(displayInfoPath, "utf8"));
  for (const param of displayInfo.Parameters ?? []) parameterIds.add(param.Id);
}

log("\n参数映射核对（lib/live2d/parameter-map.json → 该模型）：");
let missing = 0;
for (const [key, value] of Object.entries(parameterMap)) {
  if (key.startsWith("_") || typeof value !== "string") continue;
  const ok = parameterIds.size === 0 ? true : parameterIds.has(value);
  if (!ok) missing += 1;
  log(`  ${ok ? "✓" : "✗"} ${key.padEnd(13)} ${value}`);
}
log(
  parameterIds.size === 0
    ? "  （模型没有 cdi3.json，无法核对参数名——请自行确认）"
    : missing === 0
      ? "  全部命中 ✓"
      : `  有 ${missing} 项未命中，请按模型实际参数名修改 parameter-map.json`,
);
if (missing > 0) {
  log(`  该模型的参数名（前 40 个）：${[...parameterIds].slice(0, 40).join(", ")}`);
}

log(`\n可用表情（${model3.FileReferences.Expressions?.length ?? 0} 个）：`);
log(`  ${(model3.FileReferences.Expressions ?? []).map((item) => item.Name).join("、") || "（无）"}`);
log(`\n动作组：${Object.keys(model3.FileReferences.Motions ?? {}).join("、") || "（无）"}`);

log(
  [
    "",
    "[DONE] 模型导入完成。",
    "",
    "接下来按需修改两个配置文件（都有注释）：",
    "  - lib/live2d/presets.json      情绪 → 上面列出的表情名（留空 = 该情绪显示中性脸）",
    "  - lib/live2d/parameter-map.json 仅在\"未命中\"时才需要改",
    "",
    "验证：cd frontend && npm run dev",
    "",
  ].join("\n"),
);
