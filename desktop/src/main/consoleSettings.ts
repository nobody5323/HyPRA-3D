import { app } from "electron";
import fs from "node:fs/promises";
import path from "node:path";

import { normalizeConsoleSettings, type ConsoleSettings } from "../shared/ipc";

/**
 * 控制台设置持久化。
 *
 * 与 `petSettings.ts` 同一套做法（原子写 + 进程内缓存 + 坏文件回落默认）：
 * 桌面端不能因为一个被改坏的 JSON 就打不开。
 */

/** 设置文件路径：`%APPDATA%/HyPRA/console-settings.json` */
function settingsFilePath(): string {
  return path.join(app.getPath("userData"), "console-settings.json");
}

let cache: ConsoleSettings | null = null;

/** 读取控制台设置（任何读取/解析失败都归一到默认值） */
export async function loadConsoleSettings(): Promise<ConsoleSettings> {
  if (cache) {
    return cache;
  }

  try {
    const raw = await fs.readFile(settingsFilePath(), "utf8");

    cache = normalizeConsoleSettings(JSON.parse(raw));
  } catch {
    cache = normalizeConsoleSettings(undefined);
  }

  return cache;
}

/** 保存控制台设置（原子写：先写临时文件再 rename，避免留下半截 JSON） */
export async function saveConsoleSettings(settings: ConsoleSettings): Promise<void> {
  const target = settingsFilePath();
  const temporary = `${target}.tmp`;

  cache = normalizeConsoleSettings(settings);

  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.writeFile(temporary, `${JSON.stringify(cache, null, 2)}\n`, "utf8");
  await fs.rename(temporary, target);
}

/** 仅供测试：清空进程内缓存 */
export function resetConsoleSettingsCache(): void {
  cache = null;
}
