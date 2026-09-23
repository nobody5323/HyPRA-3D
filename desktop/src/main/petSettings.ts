import { app } from "electron";
import fs from "node:fs/promises";
import path from "node:path";

import { normalizePetSettings, type PetSettings } from "../shared/ipc";

/** 设置文件路径：`%APPDATA%/HyPRA/pet-settings.json` */
function settingsFilePath(): string {
  return path.join(app.getPath("userData"), "pet-settings.json");
}

/**
 * 用户自备资源的根目录：`%APPDATA%/HyPRA/live2d/<模型id>/pet.model3.json`。
 *
 * 发布包**不含** Live2D 模型（授权与体积原因），因此模型放在用户数据目录，
 * 由静态资源服务映射到 `/live2d` 前缀（见 assetServer 的 extraRoots）。
 */
export function petModelRoot(): string {
  return path.join(app.getPath("userData"), "live2d");
}

let cache: PetSettings | null = null;

/**
 * 读取设置。
 *
 * 任何读取/解析失败都归一到默认值——桌面端不能因为一个坏掉的 JSON 就打不开。
 * 结果会缓存：设置在一次运行中只变几次，但渲染层可能反复查询。
 */
export async function loadPetSettings(): Promise<PetSettings> {
  if (cache) {
    return cache;
  }

  try {
    const raw = await fs.readFile(settingsFilePath(), "utf8");

    cache = normalizePetSettings(JSON.parse(raw));
  } catch {
    cache = normalizePetSettings(undefined);
  }

  return cache;
}

/** 保存设置（原子写：先写临时文件再 rename，避免留下半截 JSON） */
export async function savePetSettings(settings: PetSettings): Promise<void> {
  const target = settingsFilePath();
  const temporary = `${target}.tmp`;

  cache = normalizePetSettings(settings);

  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.writeFile(temporary, `${JSON.stringify(cache, null, 2)}\n`, "utf8");
  await fs.rename(temporary, target);
}

/** 仅供测试：清空进程内缓存 */
export function resetPetSettingsCache(): void {
  cache = null;
}
