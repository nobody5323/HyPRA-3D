import type { PetSettings } from "../shared/ipc";
import { normalizePetSettings } from "../shared/ipc";
import type { PetDragPoint } from "../shared/ipc";

/**
 * IPC 入参校验。
 *
 * 渲染层是**不可信输入源**（页面可能被注入、preload 之外仍可能有写入路径），
 * 因此每个跨进程参数都要在类型边界上校验一次，而不是直接断言 as。
 */

/** 读取布尔入参 */
export function requireBoolean(value: unknown, name: string): boolean {
  if (typeof value !== "boolean") {
    throw new TypeError(`IPC 参数 ${name} 必须是布尔值`);
  }

  return value;
}

/** 读取拖拽坐标（必须是有限数值） */
export function requirePoint(value: unknown): PetDragPoint {
  if (!value || typeof value !== "object") {
    throw new TypeError("IPC 参数 point 必须是 { x, y } 对象");
  }

  const point = value as { x?: unknown; y?: unknown };

  if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) {
    throw new TypeError("IPC 参数 point 的 x / y 必须是有限数字");
  }

  return { x: point.x as number, y: point.y as number };
}

/**
 * 读取设置补丁。
 *
 * 只挑出**已知字段**，其余键一概丢弃：渲染层不能借 patch 往设置文件里塞任意内容
 * （设置文件会被下次启动读回）。数值最终由 `normalizePetSettings` 收敛到合法区间。
 */
export function requireSettingsPatch(value: unknown): Partial<PetSettings> {
  if (!value || typeof value !== "object") {
    throw new TypeError("IPC 参数 patch 必须是对象");
  }

  const source = value as Record<string, unknown>;
  const patch: Partial<PetSettings> = {};

  if ("scale" in source) {
    patch.scale = normalizePetSettings({ scale: source.scale }).scale;
  }

  if ("alwaysOnTop" in source) {
    patch.alwaysOnTop = requireBoolean(source.alwaysOnTop, "alwaysOnTop");
  }

  if ("clickThrough" in source) {
    patch.clickThrough = requireBoolean(source.clickThrough, "clickThrough");
  }

  return patch;
}
