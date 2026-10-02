import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { usePerceptionCapabilities } from "@/hooks/perception/usePerceptionCapabilities";
import { useSpeechInput } from "@/hooks/perception/useSpeechInput";
import type { ChatSession } from "@/hooks/chat/useChatSession";
import { PetMicButton } from "./PetMicButton";

/** 气泡里最多渲染多少条历史（窗口很小，多了只能靠滚动） */
const VISIBLE_MESSAGES = 20;

export interface ChatBubbleProps {
  session: ChatSession;
  characterLabel: string;
  onClose(): void;
}

/**
 * 桌宠的对话面板。
 *
 * 直接消费 `useChatSession`（与 Web 端同一个 hook）：记忆召回、情绪、世界书、
 * 工具调用、**以及主动消息**全在后端与 hook 里，这里只负责把消息与输入框画出来。
 *
 * 语音入口复用 Web 端的 `MicButton`（compact 模式）：面板只有 190px 宽，
 * 带文字的按钮会把输入框挤没；而「对着桌宠说话」恰恰是这个形态最自然的交互。
 * 发图入口**不放这里**——面板太窄，而它在桌宠场景里的价值也远低于语音。
 */
export function ChatBubble({ session, characterLabel, onClose }: ChatBubbleProps) {
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLDivElement | null>(null);

  const perception = usePerceptionCapabilities();
  // 转写结果**只回填输入框**，由用户确认后再发（见 useSpeechInput 的模块文档）
  const speech = useSpeechInput({
    enabled: perception.asr,
    onText: (text) => setDraft(text),
    // 快捷键同样来自插件设置；桌宠窗通常一直开着，比 Web 端更用得上
    shortcut: perception.asrShortcut,
  });

  // 新消息到达时滚到底部（聊天面板很小，不自动滚会看不到最新一句）
  useEffect(() => {
    const list = listRef.current;

    if (list) {
      list.scrollTop = list.scrollHeight;
    }
  }, [session.messages, session.busy]);

  const submit = (event: FormEvent): void => {
    event.preventDefault();

    const text = draft.trim();

    if (!text || session.busy) {
      return;
    }

    setDraft("");
    void session.send(text);
  };

  return (
    <section className="petChat" aria-label={`与${characterLabel}对话`}>
      <header className="petChatHeader">
        <span>{characterLabel}</span>
        <button type="button" onClick={onClose} title="收起对话（Esc）">
          收起
        </button>
      </header>

      <div className="petChatList" ref={listRef}>
        {session.messages.length === 0 ? (
          /*
           * 空状态顺带承担「还能语音」的提示：面板只有 190px 宽，
           * 塞不下独立的引导条（Web 端用的是 `CapabilityHint`），
           * 而这句本来就要占着这个位置。
           */
          <p className="petChatEmpty">
            {perception.showMic
              ? `说点什么吧，她会记得的。也可以点麦克风说话${
                  speech.shortcutLabel ? `，或按 ${speech.shortcutLabel}` : ""
                }。`
              : "说点什么吧，她会记得的。"}
          </p>
        ) : (
          session.messages.slice(-VISIBLE_MESSAGES).map((message, index) => (
            <p
              key={message.id ?? `${message.role}-${index}`}
              className={message.role === "user" ? "petChatUser" : "petChatAssistant"}
            >
              {message.text}
            </p>
          ))
        )}
        {session.busy ? <p className="petChatThinking">……正在想</p> : null}
      </div>

      {session.error ? <p className="petChatError">{session.error}</p> : null}
      {speech.error ? <p className="petChatError">{speech.error}</p> : null}

      <form className="petChatForm" onSubmit={submit}>
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              onClose();
            }
          }}
          placeholder={session.busy ? "正在回复…" : "说点什么…"}
          aria-label="输入消息"
        />
        <PetMicButton speech={speech} show={perception.showMic} />
        {session.busy ? (
          <button type="button" onClick={() => session.interrupt()} title="打断本轮播报">
            打断
          </button>
        ) : (
          <button type="submit" disabled={!draft.trim()}>
            发送
          </button>
        )}
      </form>
    </section>
  );
}
