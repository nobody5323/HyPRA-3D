/**
 * 语音快捷键的解析与匹配（**纯函数**，便于单测）。
 *
 * 规格串写成 `Ctrl+Shift+M` 这种人类可读形式（用户在插件设置里手填），
 * 由 `parseShortcut` 归一化后比对键盘事件。
 *
 * ## 一条安全规则
 *
 * 带 `Ctrl` / `Alt` / `Meta` 的快捷键在**任何地方**都能安全触发；
 * 而不带这些修饰键的（`M`、`F2`）在输入框里必须让路——
 * 否则用户在输入框里打一个「M」就开始录音。
 * 这条判断由 `stealsTyping` 给出，调用方据此决定要不要忽略事件。
 */

export interface ParsedShortcut {
  /** 归一化后的键名：小写；空格写成 `space`；其余用 `KeyboardEvent.key` 的小写形式 */
  key: string;
  ctrl: boolean;
  shift: boolean;
  alt: boolean;
  meta: boolean;
  /** 带 Ctrl / Alt / Meta —— 这类快捷键在输入框里也能安全触发 */
  hasCommandModifier: boolean;
}

/** 键盘事件的**最小**结构（只取用得到的字段，便于测试直接构造字面量） */
export interface KeyLike {
  key: string;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  metaKey: boolean;
}

/** 修饰键的几种常见写法 → 归一化名 */
const MODIFIER_ALIASES: Record<string, "ctrl" | "shift" | "alt" | "meta"> = {
  ctrl: "ctrl",
  control: "ctrl",
  shift: "shift",
  alt: "alt",
  option: "alt",
  meta: "meta",
  cmd: "meta",
  command: "meta",
  win: "meta",
  super: "meta",
};

/** 键名的展示别名（让 `Ctrl+Shift+Space` 这类更好读） */
const KEY_LABELS: Record<string, string> = {
  space: "Space",
  arrowup: "↑",
  arrowdown: "↓",
  arrowleft: "←",
  arrowright: "→",
  escape: "Esc",
  enter: "Enter",
  backspace: "Backspace",
  delete: "Delete",
  tab: "Tab",
};

/** 归一化单个键名 */
function normalizeKey(raw: string): string {
  const key = raw.trim().toLowerCase();
  if (key === " " || key === "space" || key === "spacebar") return "space";
  // 单字母/单数字原样；其余（F2、方向键…）取小写
  return key;
}

/**
 * 解析规格串。**无法解析时返回 null**（而不是抛错）。
 *
 * 规格来自用户在插件设置里手填的文本框，写错是常态；
 * 返回 null 让调用方「当作没配」，比让整个界面崩掉合理得多。
 */
export function parseShortcut(spec: string): ParsedShortcut | null {
  const raw = (spec || "").trim();
  if (!raw) return null;

  const parts = raw
    .split("+")
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length === 0) return null;

  let ctrl = false;
  let shift = false;
  let alt = false;
  let meta = false;
  let key = "";

  for (const part of parts) {
    const modifier = MODIFIER_ALIASES[part.toLowerCase()];
    if (modifier === "ctrl") {
      ctrl = true;
    } else if (modifier === "shift") {
      shift = true;
    } else if (modifier === "alt") {
      alt = true;
    } else if (modifier === "meta") {
      meta = true;
    } else {
      // 只允许一个非修饰键；`Ctrl+M+N` 这种是无效配置
      if (key) return null;
      key = normalizeKey(part);
    }
  }

  // 只有修饰键（如 `Ctrl+Shift`）不是合法快捷键：它没法触发
  if (!key) return null;

  return { key, ctrl, shift, alt, meta, hasCommandModifier: ctrl || alt || meta };
}

/** 事件是否命中该快捷键 */
export function matchesShortcut(event: KeyLike, spec: string): boolean {
  const parsed = parseShortcut(spec);
  if (!parsed) return false;

  if (normalizeKey(event.key) !== parsed.key) return false;

  // 修饰键必须**精确匹配**：配了 Ctrl+M 时按 Ctrl+Shift+M 不该触发，
  // 否则用户设的快捷键会与别人的快捷键互相串台
  return (
    event.ctrlKey === parsed.ctrl &&
    event.shiftKey === parsed.shift &&
    event.altKey === parsed.alt &&
    event.metaKey === parsed.meta
  );
}

/**
 * 该快捷键会不会「抢走」用户的正常输入。
 *
 * 判据只看有没有 Ctrl/Alt/Meta：`Shift+M` 仍然是字母 M，
 * 在输入框里按下它是在打大写字母，不是在喊录音。
 */
export function stealsTyping(spec: string): boolean {
  const parsed = parseShortcut(spec);
  if (!parsed) return false;
  return !parsed.hasCommandModifier;
}

/** 事件目标是否是「正在输入」的元素（输入框 / 文本域 / 可编辑区） */
export function isTypingTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  if (!element || typeof element.tagName !== "string") return false;

  const tag = element.tagName.toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  return element.isContentEditable === true;
}

/** 展示用文案：`ctrl+shift+m` → `Ctrl+Shift+M` */
export function formatShortcut(spec: string): string {
  const parsed = parseShortcut(spec);
  if (!parsed) return "";

  const parts: string[] = [];
  if (parsed.ctrl) parts.push("Ctrl");
  if (parsed.alt) parts.push("Alt");
  if (parsed.shift) parts.push("Shift");
  if (parsed.meta) parts.push("Meta");

  const label = KEY_LABELS[parsed.key];
  if (label) {
    parts.push(label);
  } else if (parsed.key.length === 1) {
    parts.push(parsed.key.toUpperCase());
  } else {
    parts.push(parsed.key.charAt(0).toUpperCase() + parsed.key.slice(1));
  }

  return parts.join("+");
}
