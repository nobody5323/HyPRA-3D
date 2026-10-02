import { app } from "electron";
import fs from "node:fs/promises";
import path from "node:path";

import { normalizeServiceSettings, type ServiceSettings } from "../shared/ipc";

/**
 * 服务设置持久化。
 *
 * 与 `consoleSettings.ts` / `petSettings.ts` 同一套做法
 * （原子写 + 进程内缓存 + 坏文件回落默认）——桌面端不能因为一个被改坏的 JSON 就打不开。
 *
 * 单独一个文件而不是并进 `console-settings.json`：这里的字段是**路径与进程开关**，
 * 用户手改坏一个路径，不该连带把控制台的界面偏好一起顶掉。
 */

/** 设置文件路径：`%APPDATA%/HyPRA/service-settings.json` */
function settingsFilePath(): string {
  return path.join(app.getPath("userData"), "service-settings.json");
}

let cache: ServiceSettings | null = null;

/** 读取服务设置（任何读取/解析失败都归一到默认值） */
export async function loadServiceSettings(): Promise<ServiceSettings> {
  if (cache) {
    return cache;
  }

  try {
    const raw = await fs.readFile(settingsFilePath(), "utf8");

    cache = normalizeServiceSettings(JSON.parse(raw));
  } catch {
    cache = normalizeServiceSettings(undefined);
  }

  return cache;
}

/** 保存服务设置（原子写：先写临时文件再 rename，避免留下半截 JSON） */
export async function saveServiceSettings(settings: ServiceSettings): Promise<void> {
  const target = settingsFilePath();
  const temporary = `${target}.tmp`;

  cache = normalizeServiceSettings(settings);

  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.writeFile(temporary, `${JSON.stringify(cache, null, 2)}\n`, "utf8");
  await fs.rename(temporary, target);
}

/** 仅供测试：清空进程内缓存 */
export function resetServiceSettingsCache(): void {
  cache = null;
}
