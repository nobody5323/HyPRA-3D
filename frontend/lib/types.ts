/** 与后端接口对齐的类型定义（backend/app/api/*.py）。 */

/** 情绪判定（M4 情绪链路输出） */
export interface EmotionInfo {
  label: string;
  label_zh: string;
  intensity: number;
  confidence: number;
  evidence?: string;
  facial_expression: string;
  source: string;
}

/** 播报指令（M5：交给 SDK 的 speak()） */
export interface SpeakCommand {
  ssml: string;
  display_text: string;
  voice: string;
  emotion: string;
  ka_action: string;
  tone: string;
  intensity: number;
  is_streaming?: boolean;
}

/** Agent 工具调用记录（P1 行动层） */
export interface ToolUsage {
  name: string;
  arguments: string;
  result: string;
}

/** POST /chat 响应 */
export interface ChatResponse {
  session_id: string;
  persona_id: string;
  reply: string;
  emotion: EmotionInfo;
  system_prompt: string;
  messages: { role: string; content: string }[];
  worldbook_hits: string[];
  skipped: string[];
  memory_counts: Record<string, number>;
  remembered: Record<string, number>;
  warnings: string[];
  estimated_tokens: number;
  style: Record<string, unknown>;
  speak: SpeakCommand;
  tools_used: ToolUsage[];
  note: string;
}

/** 一条对话消息（前端展示用） */
export interface ChatMessage {
  /** 前端本地生成的稳定 id（用作列表 key；后端不感知） */
  id?: string;
  role: "user" | "assistant";
  text: string;
}

/** 具身状态机（赛题：Listen / Think / Speak / Interrupt） */
export type AvatarState = "idle" | "listen" | "think" | "speak";

/** 具身状态的中文标签与配色 */
export const AVATAR_STATE_LABELS: Record<AvatarState, string> = {
  idle: "待机",
  listen: "聆听中",
  think: "思考中",
  speak: "说话中",
};
