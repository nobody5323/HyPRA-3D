/** 后端接口封装。 */

import type { ChatResponse, PresetCatalog } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export interface ChatRequest {
  text: string;
  session_id?: string | null;
  persona_id?: string;
  user_name?: string;
  current_mood?: string | null;
  style_id?: string | null;
  /** 模型预设 id；不传或传 null 时由后端按模型名自动匹配 */
  preset_id?: string | null;
}

/** 调用后端对话接口（对话 + 情绪 + 播报指令 + 工具调用）。 */
export async function postChat(
  body: ChatRequest,
  options: { signal?: AbortSignal } = {},
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: options.signal,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`对话请求失败（${res.status}）${detail ? `: ${detail.slice(0, 120)}` : ""}`);
  }
  return (await res.json()) as ChatResponse;
}

/** 模型预设档清单（界面「模型预设」选择器用）。 */
export async function getPresets(): Promise<PresetCatalog | null> {
  try {
    const res = await fetch(`${API_BASE}/chat/presets`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as PresetCatalog;
  } catch {
    return null;
  }
}

/** 健康检查（用于展示「后端在线」状态）。 */
export async function getHealth(): Promise<{ status: string; app: string } | null> {
  try {
    const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as { status: string; app: string };
  } catch {
    return null;
  }
}

export { API_BASE };
