/**
 * 确保 Electron 二进制存在（postinstall 里幂等执行）。
 *
 * 背景：npm 11 起，依赖包的生命周期脚本默认不再自动执行，
 * 于是 `npm install` 之后 node_modules/electron/dist 是空的——
 * 运行时才会报「找不到 electron」，排查起来很绕。
 *
 * 这里补一次：二进制已在就跳过；缺失才调用 Electron 官方的下载脚本。
 * 国内网络可先设置镜像：ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
 */
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { fileURLToPath } from "node:url";

const desktopRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const electronDir = path.join(desktopRoot, "node_modules", "electron");
const binaryName = process.platform === "win32" ? "electron.exe" : "electron";
const binaryPath = path.join(electronDir, "dist", binaryName);

if (!existsSync(electronDir)) {
  // 依赖还没装（例如只装了部分依赖），交给下一次 npm install
  console.log("[electron] 未找到 electron 包，跳过二进制检查");
} else if (existsSync(binaryPath)) {
  const version = createRequire(import.meta.url)("electron/package.json").version;

  console.log(`[electron] 二进制已就绪（v${version}），跳过下载`);
} else {
  console.log("[electron] 缺少二进制，开始下载……");
  await import(pathToFileURL(path.join(electronDir, "install.js")).href);
  console.log("[electron] 下载完成");
}
