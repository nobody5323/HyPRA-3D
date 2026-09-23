/**
 * 人设（陪伴对象）相关常量。
 *
 * 契约类型（PersonaInfo / PersonaCatalog）统一放 `lib/types.ts`，
 * 与其余后端接口类型同源；本文件只保留前端需要独立引用的常量，
 * 避免 useChatSession 反向依赖类型汇总文件。
 */

/**
 * 后端缺省人设（`backend/app/api/chat.py` 的 `_DEFAULT_PERSONA_ID`）。
 *
 * 仅在 `GET /chat/personas` 尚未返回时占位——前端不会永久依赖它：
 * 页面加载到清单后会用后端声明的 `default_persona_id` 校准。
 */
export const DEFAULT_PERSONA_ID = "therapist-elder-sister";
