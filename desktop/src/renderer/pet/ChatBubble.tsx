import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import type { ChatSession } from "@/hooks/chat/useChatSession";

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
 * 工具调用全在后端，这里只负责把消息与输入框画出来。
 */
export function ChatBubble({ session, characterLabel, onClose }: ChatBubbleProps) {
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLDivElement | null>(null);

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
          <p className="petChatEmpty">说点什么吧，她会记得的。</p>
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
