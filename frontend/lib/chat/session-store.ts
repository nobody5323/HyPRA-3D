/**
 * 本地持久化：**当前会话指针**与输入草稿（localStorage）。
 *
 * 为什么只存「指针」而不存消息：
 * 后端已把会话落 SQLite（`chat_sessions` / `chat_turns`），消息的唯一事实来源是后端。
 * 若前端也存一份消息，会出现「界面显示 10 轮、模型只看得到 1 轮」的割裂——
 * 刷新后前端渲染旧消息、后端会话却已不存在，演示「我还记得你刚说的」时会翻车。
 * 因此前端只记住「当前会话 id」，消息一律向后端要。
 *
 * 键按陪伴对象分开：每个角色各自记住自己的当前会话，切换角色不会串。
 *
 * 存储不可用时（隐私模式 / 配额满）一律静默降级：这是体验增强，不该阻断对话。
 */

const ACTIVE_SESSION_PREFIX = "hypra.active-session.";
const DRAFT_PREFIX = "hypra.draft.";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // 忽略：存不下也不影响本次会话（只是刷新后恢复不了）
  }
}

/** 读取某个陪伴对象上次使用的会话 id（无则返回 null = 新对话）。 */
export function readActiveSessionId(personaId: string): string | null {
  const value = read(ACTIVE_SESSION_PREFIX + personaId);
  return value && value.trim() ? value : null;
}

/** 记住/清除某个陪伴对象的当前会话 id（传 null 表示「新对话」）。 */
export function writeActiveSessionId(personaId: string, sessionId: string | null): void {
  write(ACTIVE_SESSION_PREFIX + personaId, sessionId);
}

/** 读取未发送的输入草稿（按陪伴对象分开）。 */
export function readDraft(personaId: string): string {
  return read(DRAFT_PREFIX + personaId) ?? "";
}

/** 保存/清除输入草稿（空串即清除）。 */
export function writeDraft(personaId: string, text: string): void {
  write(DRAFT_PREFIX + personaId, text.trim() ? text : null);
}
