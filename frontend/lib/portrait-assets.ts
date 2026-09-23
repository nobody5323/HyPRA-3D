/**
 * 立绘资源清单（静态图片渲染器的数据源）。
 *
 * 与 `avatar-renderer.ts` 的分工：那个文件管**契约**，这里管**具体资源**。
 * 换立绘只需替换 `public/portraits/<id>.svg`；换角色只需改本文件的清单，
 * 对话 / 语音 / 记忆链路一行都不用动。
 */

import {
  FALLBACK_PORTRAIT_ID,
  PORTRAIT_IDS,
  resolvePortraitId,
  type AvatarExpressionEntry,
  type PortraitId,
} from "./avatar-renderer";

/** 立绘静态资源目录（Next.js `public/` 下的路径） */
export const PORTRAIT_BASE_PATH = "/portraits";

/**
 * 立绘展示名（读屏与调试用）。
 *
 * 导出而不只内部使用：Live2D 渲染器同样需要「情绪 id → 中文名」的映射，
 * 两个渲染器共用同一份标签，避免同一种情绪在两处叫不同名字。
 *
 * 声明为 `Record<PortraitId, string>`：将来新增情绪 id 时**编译期**就会在这里报错，
 * 不会留下「有 id、没名字」的半成品状态。
 */
export const PORTRAIT_LABELS: Record<PortraitId, string> = {
  happy: "开心",
  calm: "平静",
  sad: "难过",
  anxious: "焦虑",
  tired: "疲惫",
  angry: "生气",
  surprised: "惊讶",
  neutral: "中性",
};

/**
 * 立绘资源条目。
 *
 * `url` 在本清单里**一定存在**（文件名由 id 派生，不存在"有 id 没图"的情况），
 * 所以这里用交叉类型把可选字段收紧——调用方不必为 `undefined` 写防御代码。
 */
export type PortraitEntry = AvatarExpressionEntry & { url: string };

/**
 * 立绘清单：id → 资源条目。
 *
 * 文件名与 id 同名（`public/portraits/happy.svg`），因此清单直接从 `PORTRAIT_IDS`
 * 派生——少一处需要手工同步的映射，也就少一处会写错的映射。
 */
export const PORTRAIT_MANIFEST: Record<PortraitId, PortraitEntry> = Object.freeze(
  Object.fromEntries(
    PORTRAIT_IDS.map((id) => [
      id,
      { id, name: PORTRAIT_LABELS[id], url: `${PORTRAIT_BASE_PATH}/${id}.svg` },
    ]),
  ) as Record<PortraitId, PortraitEntry>,
);

/**
 * 按立绘 id 取资源条目。
 *
 * 未知 id 一律回落 `neutral`，因此**永远返回可用条目**，调用方无需判空。
 */
export function resolvePortraitEntry(id: string | null | undefined): PortraitEntry {
  return PORTRAIT_MANIFEST[resolvePortraitId(id)] ?? PORTRAIT_MANIFEST[FALLBACK_PORTRAIT_ID];
}

/**
 * 情绪信息 → 立绘条目。
 *
 * 传 `EmotionInfo` 的子集而不是裸字符串：**优先英文 `label`**（唯一），
 * 缺失时才退回 `label_zh`（后端 calm 与 neutral 的中文都是「平静」，有歧义）。
 *
 * @param options.portraitUrl 上传模型的立绘来源；该情绪没指定时回落内置占位立绘，
 *                            而不是返回空——没指定的情绪必须有兜底。
 */
export function resolveEmotionPortrait(
  emotion: { label?: string | null; label_zh?: string | null } | null | undefined,
  options: { portraitUrl?: ((portraitId: PortraitId) => string | null) | null } = {},
): PortraitEntry {
  const candidate = emotion?.label?.trim() || emotion?.label_zh?.trim() || "";
  const id = resolvePortraitId(candidate);
  const external = options.portraitUrl?.(id);
  if (external) return { id, name: PORTRAIT_LABELS[id], url: external };
  return resolvePortraitEntry(candidate);
}
