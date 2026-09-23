/**
 * Cubism 运行时桥的**降级实现**（提交入库）。
 *
 * 当本地没有安装 Cubism SDK 时，`@cubism-bridge` 的 alias 会解析到本文件：
 * - `available === false`，调用方（Live2D 渲染器 / 探测逻辑）据此立即降级到静态立绘；
 * - `load()` 直接 reject，这样即便有人绕过 `available` 判断，也只会拿到明确错误，
 *   而不是"渲染出一片空白却以为成功"。
 *
 * 本文件**不得** import 任何官方 SDK 内容——它在没有 SDK 的环境里也必须能编译。
 */

import { CUBISM_UNAVAILABLE_REASON } from "./cubism-core";
import type { CubismBridge, CubismModelHandle, CubismLoadOptions } from "./cubism-bridge";

export const cubismBridge: CubismBridge = {
  available: false,
  unavailableReason: CUBISM_UNAVAILABLE_REASON,
  init(): void {
    // 无 SDK 可初始化：静默返回（调用方应先看 available）
  },
  load(_options: CubismLoadOptions): Promise<CubismModelHandle> {
    return Promise.reject(new Error(CUBISM_UNAVAILABLE_REASON));
  },
};

export default cubismBridge;
