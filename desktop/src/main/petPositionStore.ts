import { app } from "electron";
import fs from "node:fs/promises";
import path from "node:path";

import { parseSavedPosition } from "./petGeometry";

/** 位置文件路径：`%APPDATA%/HyPRA/pet-window.json`（用户数据，不入库） */
function positionFilePath(): string {
  return path.join(app.getPath("userData"), "pet-window.json");
}

/**
 * 读取上次保存的窗口位置。
 *
 * 任何读取/解析失败都视作「没有记录」——桌宠停在默认位置总好过启动失败。
 */
export async function loadPetWindowPosition(): Promise<
  { x: number; y: number } | undefined
> {
  try {
    const raw = await fs.readFile(positionFilePath(), "utf8");

    return parseSavedPosition(JSON.parse(raw));
  } catch {
    return undefined;
  }
}

/**
 * 保存窗口位置（原子写：先写临时文件再 rename）。
 *
 * 拖拽结束时可能连续触发多次保存，直接覆写有概率留下半截 JSON；
 * rename 在同一分区上是原子操作，读取方要么看到旧内容、要么看到新内容。
 */
export async function savePetWindowPosition(position: {
  x: number;
  y: number;
}): Promise<void> {
  const target = positionFilePath();
  const temporary = `${target}.tmp`;

  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.writeFile(temporary, `${JSON.stringify(position, null, 2)}\n`, "utf8");
  await fs.rename(temporary, target);
}
