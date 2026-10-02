import type { ConsoleSettings, PetSettings, ServiceId, ServiceSettings } from "../shared/ipc";
import { normalizePetSettings, normalizeWebUrl, SERVICE_PATH_FIELDS } from "../shared/ipc";
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

/**
 * 读取控制台设置补丁。
 *
 * 只挑已知字段；`webUrl` 归一化失败就**报错**，而不是静静回落成默认值：
 * 用户明明填了东西却没生效（比如填成了 `file:///...`），比一句明确的报错难查得多。
 * 这一层是 `shell.openExternal` 的最后一道门，协议白名单在 `normalizeWebUrl` 里。
 */
export function requireConsoleSettingsPatch(value: unknown): Partial<ConsoleSettings> {
  if (!value || typeof value !== "object") {
    throw new TypeError("IPC 参数 patch 必须是对象");
  }

  const source = value as Record<string, unknown>;
  const patch: Partial<ConsoleSettings> = {};

  if ("webUrl" in source) {
    const normalized = normalizeWebUrl(source.webUrl);

    if (!normalized) {
      throw new TypeError("webUrl 必须是 http/https 地址");
    }

    patch.webUrl = normalized;
  }

  return patch;
}

/**
 * 读取受管服务标识。
 *
 * 白名单而不是 `as ServiceId`：渲染层是**不可信输入源**，一个字面量写歪了就会
 * 在管理器里查到不存在的服务并抛错，不如在边界上直接挡下来。
 */
export function requireServiceId(value: unknown): ServiceId {
  if (value !== "qdrant" && value !== "backend" && value !== "frontend") {
    throw new TypeError("IPC 参数 id 必须是 qdrant、backend 或 frontend");
  }

  return value;
}

/**
 * 读取服务设置补丁。
 *
 * 路径字段只接受字符串（空串 = 清除覆盖，回落到自动探测），
 * 布尔字段必须是真布尔值：服务设置会被下次启动读回，不能靠隐式转换蒙混过关。
 */
export function requireServiceSettingsPatch(value: unknown): Partial<ServiceSettings> {
  if (!value || typeof value !== "object") {
    throw new TypeError("IPC 参数 patch 必须是对象");
  }

  const source = value as Record<string, unknown>;
  const patch: Partial<ServiceSettings> = {};

  if ("autoStart" in source) {
    patch.autoStart = requireBoolean(source.autoStart, "autoStart");
  }

  if ("backendReload" in source) {
    patch.backendReload = requireBoolean(source.backendReload, "backendReload");
  }

  for (const field of SERVICE_PATH_FIELDS) {
    if (field in source) {
      const raw = source[field];

      if (typeof raw !== "string") {
        throw new TypeError(`IPC 参数 ${field} 必须是字符串路径`);
      }

      patch[field] = raw.trim();
    }
  }

  return patch;
}
