/**
 * 用户称呼（对话里的 `{{user_name}}`）。
 *
 * 与角色名对称：角色名由人设决定，用户称呼由用户自己定——两者都不该写死在组件里。
 * 存的是**共享偏好**（后端 `data/chat-preferences.json`），控制台 / Web 端 / 桌宠窗
 * 读同一份（三者 localStorage 不互通）。
 */

/**
 * 默认称呼。
 *
 * 与后端 `app/prompts/state_vars/definitions.py` 的 `user_name.default` 一致：
 * 没设置过时用「朋友」，而不是某个具体人名——后者会让用户以为产品在假设他是谁。
 */
export const DEFAULT_USER_NAME = "朋友";

/** 长度上限（与后端 `api/chat.py` 的 `_MAX_USER_NAME_LENGTH` 保持一致） */
export const USER_NAME_MAX_LENGTH = 24;

/**
 * 规范化用户输入：去首尾空白 + 截断。
 *
 * 空串表示「清除」，界面回落到 `DEFAULT_USER_NAME`——与后端「空串 = 没设置过」
 * 的语义一致，所以这里不偷偷补默认值（那会让「清除」变得不可能）。
 */
export function normalizeUserName(raw: string): string {
  return raw.trim().slice(0, USER_NAME_MAX_LENGTH);
}
