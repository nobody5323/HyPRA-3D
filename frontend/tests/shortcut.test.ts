/**
 * 语音快捷键的解析与匹配。
 *
 * 这一层只有两条设计意图要守住，而两条都对应真实会烦到人的失败：
 *
 * 1. **不带 Ctrl/Alt/Meta 的快捷键在输入框里必须让路**——否则用户打字
 *    打到 M 就开始录音；
 * 2. **修饰键精确匹配**——配了 Ctrl+M 时按 Ctrl+Shift+M 不该触发，
 *    否则用户设的快捷键会与别人的快捷键互相串台。
 */

import { describe, expect, it } from "vitest";

import {
  formatShortcut,
  isTypingTarget,
  matchesShortcut,
  parseShortcut,
  stealsTyping,
  type KeyLike,
} from "@/lib/chat/shortcut";

/** 构造一个键盘事件字面量（默认无修饰键、key 为空） */
function key(k: string, modifiers: Partial<Omit<KeyLike, "key">> = {}): KeyLike {
  return {
    key: k,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    metaKey: false,
    ...modifiers,
  };
}

describe("parseShortcut", () => {
  it("解析常见写法", () => {
    const parsed = parseShortcut("Ctrl+Shift+M");
    expect(parsed).toMatchObject({ key: "m", ctrl: true, shift: true, hasCommandModifier: true });
  });

  it("大小写与空白不敏感", () => {
    expect(parseShortcut("  ctrl + shift + m  ")).toMatchObject({ key: "m", ctrl: true });
  });

  it("识别修饰键的别名", () => {
    expect(parseShortcut("Cmd+M")).toMatchObject({ meta: true });
    expect(parseShortcut("Option+M")).toMatchObject({ alt: true });
    expect(parseShortcut("Win+M")).toMatchObject({ meta: true });
  });

  it("空格键归一化", () => {
    expect(parseShortcut("Ctrl+Space")).toMatchObject({ key: "space", ctrl: true });
  });

  it("不带修饰键也算合法（如 F2）", () => {
    const parsed = parseShortcut("F2");
    expect(parsed).toMatchObject({ key: "f2", hasCommandModifier: false });
  });

  it("空串与纯空白 → null", () => {
    expect(parseShortcut("")).toBeNull();
    expect(parseShortcut("   ")).toBeNull();
  });

  it("只有修饰键 → null（它没法触发）", () => {
    expect(parseShortcut("Ctrl+Shift")).toBeNull();
  });

  it("多个非修饰键 → null（`Ctrl+M+N` 是无效配置）", () => {
    expect(parseShortcut("Ctrl+M+N")).toBeNull();
  });
});

describe("matchesShortcut", () => {
  it("命中", () => {
    expect(matchesShortcut(key("m", { ctrlKey: true, shiftKey: true }), "Ctrl+Shift+M")).toBe(true);
  });

  it("键名大小写不敏感", () => {
    expect(matchesShortcut(key("M", { ctrlKey: true }), "Ctrl+M")).toBe(true);
  });

  it("修饰键**精确**匹配：多按一个 Shift 就不算命中", () => {
    expect(
      matchesShortcut(key("m", { ctrlKey: true, shiftKey: true }), "Ctrl+M"),
    ).toBe(false);
    expect(matchesShortcut(key("m", { ctrlKey: true }), "Ctrl+Shift+M")).toBe(false);
  });

  it("少按修饰键不算命中", () => {
    expect(matchesShortcut(key("m"), "Ctrl+M")).toBe(false);
  });

  it("未配置快捷键时永不命中", () => {
    expect(matchesShortcut(key("m", { ctrlKey: true }), "")).toBe(false);
  });

  it("无效配置不抛错，只是不命中", () => {
    expect(matchesShortcut(key("m", { ctrlKey: true }), "Ctrl+Shift")).toBe(false);
  });
});

describe("stealsTyping", () => {
  it("带 Ctrl/Alt/Meta 的快捷键在输入框里也安全", () => {
    expect(stealsTyping("Ctrl+M")).toBe(false);
    expect(stealsTyping("Alt+M")).toBe(false);
    expect(stealsTyping("Cmd+M")).toBe(false);
  });

  it("Shift 不算「命令修饰键」：Shift+M 仍然是在打大写字母", () => {
    expect(stealsTyping("Shift+M")).toBe(true);
  });

  it("裸键会抢输入", () => {
    expect(stealsTyping("M")).toBe(true);
  });

  it("未配置时不算抢输入", () => {
    expect(stealsTyping("")).toBe(false);
  });
});

describe("isTypingTarget", () => {
  const target = (tag: string, contentEditable = false) =>
    ({ tagName: tag, isContentEditable: contentEditable }) as unknown as EventTarget;

  it("输入框 / 文本域 / 下拉框都算", () => {
    expect(isTypingTarget(target("INPUT"))).toBe(true);
    expect(isTypingTarget(target("TEXTAREA"))).toBe(true);
    expect(isTypingTarget(target("SELECT"))).toBe(true);
  });

  it("可编辑区算", () => {
    expect(isTypingTarget(target("DIV", true))).toBe(true);
  });

  it("普通元素不算", () => {
    expect(isTypingTarget(target("DIV"))).toBe(false);
    expect(isTypingTarget(target("BUTTON"))).toBe(false);
  });

  it("null / 非元素不算（不抛错）", () => {
    expect(isTypingTarget(null)).toBe(false);
    expect(isTypingTarget({} as EventTarget)).toBe(false);
  });
});

describe("formatShortcut", () => {
  it("按 Ctrl → Alt → Shift → Meta 的顺序展示", () => {
    expect(formatShortcut("shift+ctrl+m")).toBe("Ctrl+Shift+M");
  });

  it("单字母大写", () => {
    expect(formatShortcut("ctrl+m")).toBe("Ctrl+M");
  });

  it("特殊键用可读别名", () => {
    expect(formatShortcut("Ctrl+Space")).toBe("Ctrl+Space");
    expect(formatShortcut("Alt+ArrowUp")).toBe("Alt+↑");
    expect(formatShortcut("Escape")).toBe("Esc");
  });

  it("未配置或无效 → 空串（调用方据此不显示提示）", () => {
    expect(formatShortcut("")).toBe("");
    expect(formatShortcut("Ctrl+Shift")).toBe("");
  });
});
