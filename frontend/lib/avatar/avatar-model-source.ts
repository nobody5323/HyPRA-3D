/**
 * 把「选中的模型」翻译成渲染层能直接用的形状。
 *
 * 渲染器不应该知道模型是内置的还是上传的——它只接受 URL。这层负责统一：
 * - 没选模型（或后端拿不到清单）→ `builtin`：用项目内置资源；
 * - 选中的是 Live2D → 给出入口 URL（表情由模型自带 exp3.json 提供）；
 * - 选中的是静态立绘 → 给出「情绪 → 图片 URL」，**未指定的情绪返回 null**
 *   （渲染层回落内置占位立绘，而不是什么都不显示）。
 *
 * 用可辨识联合而不是若干可空字段：`live2dUrl === null` 既可能是"用内置 Live2D"、
 * 也可能是"选了静态立绘模型"，两者行为完全不同，用可空字段必然写错。
 */

import { avatarModelFileUrl } from "@/lib/api/client";
import type { PortraitId } from "./avatar-renderer";
import type { AvatarModelInfo } from "@/lib/api/types";

export type AvatarModelSource =
  | { kind: "builtin" }
  | { kind: "live2d"; modelUrl: string }
  | { kind: "images"; portraitUrl: (portraitId: PortraitId) => string | null };

/** 内置模型（没有选用任何上传模型时的落点） */
export const BUILT_IN_MODEL_SOURCE: AvatarModelSource = { kind: "builtin" };

export function resolveAvatarModelSource(model: AvatarModelInfo | null): AvatarModelSource {
  if (!model) return BUILT_IN_MODEL_SOURCE;

  if (model.kind === "live2d") {
    // entry 是相对 files/ 的路径（zip 里可能套了一层文件夹），为空说明模型不完整
    if (!model.entry) return BUILT_IN_MODEL_SOURCE;
    return { kind: "live2d", modelUrl: avatarModelFileUrl(model.id, model.entry) };
  }

  const { id, expressionMap } = model;
  return {
    kind: "images",
    portraitUrl: (portraitId: PortraitId) => {
      const filename = expressionMap[portraitId];
      return filename ? avatarModelFileUrl(id, filename) : null;
    },
  };
}
