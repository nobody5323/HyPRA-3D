/**
 * Live2D 模型清单。
 *
 * 目录约定（与 `scripts/setup-cubism.mjs` 的输出说明、`.gitignore` 一致）：
 *
 * ```
 * frontend/public/live2d/<模型id>/pet.model3.json   ← 入口（固定文件名）
 *                              ├── *.moc3
 *                              ├── *.png
 *                              ├── *.physics3.json
 *                              ├── *.exp3.json
 *                              └── *.motion3.json
 * ```
 *
 * 模型**不入库**（授权与体积原因），所以这里只登记"从哪里取"，
 * 不登记模型内容。换模型 = 放目录 + 改本文件。
 */

import type { AvatarPortraitLayout } from "@/lib/avatar/avatar-renderer";

/** 模型根目录（Next.js `public/` 下的路径） */
export const LIVE2D_MODEL_BASE_PATH = "/live2d";

/** 入口文件名：固定，避免每个模型都要额外配置 */
export const LIVE2D_MODEL_ENTRY = "pet.model3.json";

/** 默认模型 id（找不到清单项时的兜底） */
export const DEFAULT_LIVE2D_MODEL_ID = "default";

/** 一个 Live2D 模型 */
export interface Live2DModelEntry {
  id: string;
  /** 展示名（诊断面板用） */
  name: string;
  /** 入口 `pet.model3.json` 的 URL */
  modelUrl: string;
  /**
   * 构图校准。
   *
   * 不同模型的画布比例与人物在画布中的位置差别很大（半身 / 全身 / 偏移），
   * 用同一套 CSS 必然有的模型显示不全，所以校准参数随模型登记。
   */
  layout?: AvatarPortraitLayout;
}

function modelUrlOf(id: string): string {
  return `${LIVE2D_MODEL_BASE_PATH}/${id}/${LIVE2D_MODEL_ENTRY}`;
}

/**
 * 模型清单。
 *
 * 默认只有 `default` 一项：把你的模型目录放到 `public/live2d/default/` 即可生效，
 * 无需改动本文件。要用多个模型时在这里追加条目。
 */
export const LIVE2D_MODELS: Record<string, Live2DModelEntry> = {
  default: {
    id: "default",
    name: "默认模型",
    modelUrl: modelUrlOf("default"),
    layout: { scale: 1, anchor: "bottom center" },
  },
};

/** 按 id 取模型条目；未知 id 回落默认模型（永远返回可用条目） */
export function resolveLive2DModel(id?: string | null): Live2DModelEntry {
  const key = String(id ?? "").trim() || DEFAULT_LIVE2D_MODEL_ID;
  return LIVE2D_MODELS[key] ?? LIVE2D_MODELS[DEFAULT_LIVE2D_MODEL_ID];
}
