/**
 * 人设（陪伴对象）相关常量与取名工具。
 *
 * 契约类型（PersonaInfo / PersonaCatalog）统一放 `lib/api/types`，
 * 与其余后端接口类型同源；本文件对它是 **type-only 导入**（编译后完全擦除），
 * 因此不会给 useChatSession 之类的调用方引入运行时依赖。
 */

import type { PersonaCatalog } from "@/lib/api/types";

/**
 * 后端缺省人设（`backend/app/api/chat.py` 的 `_DEFAULT_PERSONA_ID`）。
 *
 * 仅在 `GET /chat/personas` 尚未返回时占位——前端不会永久依赖它：
 * 页面加载到清单后会用后端声明的 `default_persona_id` 校准。
 */
export const DEFAULT_PERSONA_ID = "therapist-elder-sister";

/**
 * 角色名的中性兜底词（清单未回来、或 id 查不到时用）。
 *
 * **刻意不退回某个具体角色名**：内置人设只是示例之一，不是这个产品的风格，
 * 用户要什么角色都可以。切换人设后界面还显示「苏澄」，比短暂留白更容易
 * 让人以为产品只有一种角色。
 */
export const PERSONA_NAME_FALLBACK = "角色";

/** 取某个人设的展示名（清单缺失时用中性兜底词）。 */
export function personaName(catalog: PersonaCatalog | null, personaId: string): string {
  const found = catalog?.personas.find((persona) => persona.id === personaId);
  return found?.name ?? PERSONA_NAME_FALLBACK;
}

/** 取某个人设的一句话定位（清单缺失时返回空串，由调用方决定要不要渲染这一行）。 */
export function personaTitle(catalog: PersonaCatalog | null, personaId: string): string {
  return catalog?.personas.find((persona) => persona.id === personaId)?.title ?? "";
}
