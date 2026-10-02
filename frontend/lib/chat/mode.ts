/**
 * 交互模式（对应后端 `backend/app/session/mode.py`，AGENTS.md §8.1）。
 *
 * | 模式 | 定位 | 提示词来源 |
 * | --- | --- | --- |
 * | `companion` | 桌宠对话，跨会话长期陪伴 | HyPRA 分层提示词 + 世界书 + 记忆四层 |
 * | `tavern` | 酒馆聊天，还原酒馆原生体验 | 仅酒馆预设 + 角色卡 |
 *
 * 界面**不自己推断模式**：它由后端按「显式请求 > ST 预设 > 会话既有」定出来并随
 * 响应回传（`ChatResponse.mode`）。前端只负责把它显示清楚——用户在桌宠模式下
 * 找不到酒馆世界书时，第一件该看到的就是「你现在是桌宠模式」。
 */

export const MODE_COMPANION = "companion";
export const MODE_TAVERN = "tavern";

/** 模式的界面名（未知值返回空串：宁可不显示，也不要显示一个错的名字）。 */
export function modeLabel(mode: string | undefined): string {
  if (mode === MODE_TAVERN) return "酒馆聊天";
  if (mode === MODE_COMPANION) return "桌宠对话";
  return "";
}
