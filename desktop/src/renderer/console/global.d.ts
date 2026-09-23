import type { ConsoleBridge } from "../../shared/ipc";

/**
 * preload（src/preload/console.ts）注入的控制台能力。
 *
 * 声明为可选：同一个页面在浏览器里直接打开（调试渲染层时）没有这组 API，
 * 组件需要能优雅降级而不是直接报错。
 */
declare global {
  interface Window {
    hyprConsole?: ConsoleBridge;
  }
}

export {};
