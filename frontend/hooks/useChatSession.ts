"use client";

/**
 * 对话会话编排：串起「具身状态机 + 后端请求 + 播报 + 情绪视觉」。
 *
 * 流程（与赛题要求的 Listen/Think/Speak/Interrupt 对齐）：
 *   listen（记录用户输入）→ think（等待后端）→ speak（播报 + 字幕）→ idle
 *
 * 稳定性设计：
 * - `turnRef` 回合序号：发送与打断都会自增，旧回合的后续写入全部失效
 *   （否则打断后旧回合会把新回合状态打回 idle，且第二次「打断」静默失效）；
 * - `avatar` / `busy` 经 ref 读取，使 send / interrupt 身份保持稳定；
 * - 每条消息带稳定 id，供列表 key 使用（避免用数组下标）。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { postChat } from "@/lib/api";
import type { ChatMessage, EmotionInfo, ToolUsage } from "@/lib/types";
import type { AvatarController } from "./useAvatar";

export interface ChatSession {
  messages: ChatMessage[];
  emotion: EmotionInfo | null;
  toolsUsed: ToolUsage[];
  subtitle: string;
  tone: string;
  memoryCounts: Record<string, number>;
  sessionId: string | null;
  styleId: string;
  setStyleId: (id: string) => void;
  /** 模型预设 id；"" = 自动（后端按模型名匹配） */
  presetId: string;
  setPresetId: (id: string) => void;
  busy: boolean;
  error: string | null;
  send: (text: string) => Promise<void>;
  interrupt: () => void;
}

export function useChatSession(avatar: AvatarController, userName = "小林"): ChatSession {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [emotion, setEmotion] = useState<EmotionInfo | null>(null);
  const [toolsUsed, setToolsUsed] = useState<ToolUsage[]>([]);
  const [subtitle, setSubtitle] = useState("");
  const [tone, setTone] = useState("");
  const [memoryCounts, setMemoryCounts] = useState<Record<string, number>>({});
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [styleId, setStyleId] = useState("modern-conversational");
  const [presetId, setPresetId] = useState(""); // "" = 自动（按模型名匹配）
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  /** 回合序号：发送与打断都会自增；旧回合据此判断自己已被作废 */
  const turnRef = useRef(0);
  /** 消息 id 自增计数（仅用于前端列表 key） */
  const messageIdRef = useRef(0);

  // avatar / busy 经 ref 读取：让 send、interrupt 的身份保持稳定
  const avatarRef = useRef(avatar);
  useEffect(() => {
    avatarRef.current = avatar;
  }, [avatar]);
  const busyRef = useRef(busy);
  busyRef.current = busy;

  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || busyRef.current) return;

      const turn = ++turnRef.current;
      /** 本回合是否仍是当前回合（被打断 / 被新回合取代后为 false） */
      const isCurrent = () => turnRef.current === turn;
      const currentAvatar = avatarRef.current;

      setError(null);
      setBusy(true);
      currentAvatar.setState("listen"); // ① 聆听
      setMessages((prev) => [
        ...prev,
        { id: `msg-${++messageIdRef.current}`, role: "user", text: content },
      ]);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        currentAvatar.setState("think"); // ② 思考（等待后端）
        const res = await postChat(
          {
            text: content,
            session_id: sessionId,
            style_id: styleId,
            user_name: userName,
            preset_id: presetId || null,
          },
          { signal: controller.signal },
        );
        // 已被打断 / 被新回合取代：丢弃结果，不写任何界面状态
        if (!isCurrent()) return;

        setSessionId(res.session_id);
        setMessages((prev) => [
          ...prev,
          { id: `msg-${++messageIdRef.current}`, role: "assistant", text: res.reply },
        ]);
        setEmotion(res.emotion ?? null);
        setToolsUsed(res.tools_used ?? []);
        setSubtitle(res.speak?.display_text || res.reply);
        setTone(res.speak?.tone ?? "");
        setMemoryCounts(res.memory_counts ?? {});

        currentAvatar.setState("speak"); // ③ 播报（文本给浏览器 TTS，SSML 给魔珐 SDK）
        await currentAvatar.speak(res.speak?.display_text || res.reply, res.speak?.ssml);
        if (!isCurrent()) return; // 播报期间被打断 → 状态已由 interrupt() 处理
        currentAvatar.setState("idle"); // ④ 回到待机
      } catch (err) {
        if (!isCurrent()) return; // 旧回合的失败不得影响新回合界面
        if ((err as Error).name === "AbortError") {
          // 打断属用户主动行为：interrupt() 已清掉提示，这里不再当错误展示
          currentAvatar.setState("idle");
        } else {
          setError((err as Error).message || "对话失败，请检查后端是否已启动。");
          currentAvatar.setState("idle");
        }
      } finally {
        // 只清理本回合：否则旧回合会把新回合的 controller 清空，
        // 导致第二次「打断」静默失效
        if (isCurrent()) {
          setBusy(false);
          abortRef.current = null;
        }
      }
    },
    [sessionId, styleId, presetId, userName],
  );

  /** 打断：立即停止播报并中止请求（客户端即时打断，不等服务端）。 */
  const interrupt = useCallback(() => {
    turnRef.current += 1; // 作废进行中的回合（其后续 setState 全部失效）
    abortRef.current?.abort();
    abortRef.current = null;
    avatarRef.current.interrupt();
    setBusy(false);
    setError(null); // 打断是用户主动行为，不作为错误提示
  }, []);

  return {
    messages,
    emotion,
    toolsUsed,
    subtitle,
    tone,
    memoryCounts,
    sessionId,
    styleId,
    setStyleId,
    presetId,
    setPresetId,
    busy,
    error,
    send,
    interrupt,
  };
}
