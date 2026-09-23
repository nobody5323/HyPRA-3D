import type { PetBridge } from "../../shared/ipc";

/**
 * preload（src/preload/pet.ts）注入的桌面能力。
 *
 * 声明为可选：同一个页面在浏览器里直接打开（调试渲染层时）没有这组 API，
 * 组件需要能优雅降级而不是直接报错。
 */
declare global {
  interface Window {
    hyprPet?: PetBridge;
  }
}

export {};
