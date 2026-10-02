"use client";

/**
 * 「你希望角色怎么称呼你」输入框（`{{user_name}}` 的取值）。
 *
 * 存的是**共享偏好**（后端 `data/chat-preferences.json`）：控制台、Web 端、桌宠窗
 * 读同一份，所以在这里改完，另两端下次打开就用新称呼（三者 localStorage 不互通）。
 *
 * 与 persona / style 的差别：它不影响会话归属与记忆隔离，只改提示词里的称呼，
 * 因此**不切会话**——正在聊的那段对话，下一轮就用新称呼。
 *
 * 交互：受控输入 + 显式保存。不做「失焦自动保存」：切窗口看别的资料时会被
 * 意外写盘，而且没有明确的位置告诉用户存没存上。
 */

import { useEffect, useState } from "react";

import { normalizeUserName, USER_NAME_MAX_LENGTH } from "@/lib/chat/user";

const INPUT_CLASS =
  "focus-ring w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint disabled:opacity-60";

export function UserNameField({
  value,
  onSave,
  disabled = false,
  idPrefix,
}: {
  /** 当前生效的称呼（来自偏好，或默认值「朋友」） */
  value: string;
  /** 保存；失败时抛错（组件把原因显示在输入框下方） */
  onSave: (name: string) => Promise<void>;
  disabled?: boolean;
  idPrefix: string;
}) {
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fieldId = `${idPrefix}-user-name`;

  // 外部值变化（偏好拉回来 / 另一端改过）时同步草稿。会覆盖正在输入的内容，
  // 但 value 只在「加载完成」与「保存成功」两处变化，那时 draft 本就该跟着走。
  useEffect(() => {
    setDraft(value);
  }, [value]);

  const pending = normalizeUserName(draft);
  const dirty = pending !== value;

  async function save() {
    setSaving(true);
    setError(null);
    setStatus(null);
    try {
      await onSave(pending);
      setStatus(
        pending ? `已保存：角色以后会叫你「${pending}」` : "已清除：角色会用通用称呼叫你",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 card px-4 py-3">
      <label htmlFor={fieldId} className="text-xs font-medium text-ink">
        你希望角色怎么称呼你
      </label>
      <div className="flex items-center gap-2">
        <input
          id={fieldId}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setStatus(null);
          }}
          // Enter 保存：这是单行输入，键盘用户不该被迫去点按钮
          onKeyDown={(event) => {
            if (event.key === "Enter" && dirty && !saving && !disabled) void save();
          }}
          maxLength={USER_NAME_MAX_LENGTH}
          disabled={disabled || saving}
          placeholder="例如：阿岸"
          className={INPUT_CLASS}
        />
        <button
          type="button"
          onClick={() => void save()}
          disabled={disabled || saving || !dirty}
          className="focus-ring shrink-0 rounded-lg border border-line px-3 py-2 text-xs font-medium text-ink-muted transition-colors hover:bg-surface-hover disabled:cursor-not-allowed disabled:text-ink-faint"
        >
          {saving ? "保存中…" : "保存"}
        </button>
      </div>
      <p className="text-[11px] leading-relaxed text-ink-faint">
        改的是你自己的称呼（角色名由人设决定）。留空保存 = 清除，
        角色会用一个通用称呼，不会自己给你起名字。
      </p>
      {status ? (
        <p role="status" className="text-[11px] leading-relaxed text-success-text">
          {status}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="text-[11px] leading-relaxed text-danger-text">
          {error}
        </p>
      ) : null}
    </div>
  );
}
