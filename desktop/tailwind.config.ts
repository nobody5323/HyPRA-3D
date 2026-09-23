import path from "node:path";
import { fileURLToPath } from "node:url";

import type { Config } from "tailwindcss";

import base from "../frontend/tailwind.config";

/**
 * 控制台窗的 Tailwind 配置。
 *
 * **主题从 Web 端 import，不在这里重写一遍**：色板、动画、阴影这些一旦两处各写一份，
 * 迟早会出现「同一个语义色在两个端不一样」的问题。这里只覆盖 `content`——
 * 它必须覆盖到真正被渲染的文件（Web 端的组件 + 桌面端控制台的渲染层）。
 *
 * 路径用绝对路径：tailwind 的 `content` 默认相对进程工作目录解析，而构建与
 * 开发期的工作目录不保证一致。
 */
const currentDir = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(currentDir, "../frontend");

const config: Config = {
  ...base,
  content: [
    path.join(currentDir, "src/renderer/console/**/*.{ts,tsx}"),
    // 复用 Web 端的组件：它们的类名必须被扫到，否则样式整片丢失
    path.join(frontendDir, "components/**/*.{ts,tsx}"),
    path.join(frontendDir, "app/globals.css"),
  ],
};

export default config;
