/**
 * ST 预设面板的纯逻辑测试。
 *
 * 覆盖最容易出错的三类规则：
 * 1. 顺序表是权威（`order` 决定先后与启用，未进顺序的条目视为未启用）；
 * 2. 补丁合并必须深合并（连续改两个条目时前一次改动不能被整个对象覆盖掉）；
 * 3. 补丁里的 null 表示「恢复预设原值」，合并时不能被当成空值丢掉。
 */

import { describe, expect, it } from "vitest";

import {
  SUPPORTED_TRIGGERS,
  describeRow,
  estimatePresetTokens,
  estimateTokens,
  formatImportedAt,
  isEmptyPatch,
  joinOrderWithPrompts,
  mergePatch,
  moveRow,
  reorderRows,
  rowPatch,
  toOrderPatch,
  updateRow,
} from "@/lib/st-preset";
import type { StPresetDetail, StPromptItem } from "@/lib/types";

function makeItem(overrides: Partial<StPromptItem> = {}): StPromptItem {
  return {
    identifier: "main",
    name: "Main",
    role: "system",
    content: "（测试语料）正文",
    marker: false,
    system_prompt: true,
    forbid_overrides: false,
    injection_position: 0,
    injection_depth: 4,
    injection_order: 100,
    injection_trigger: [],
    marker_source: "",
    content_editable: true,
    ...overrides,
  };
}

function makeDetail(overrides: Partial<StPresetDetail> = {}): StPresetDetail {
  return {
    id: "demo",
    sampling: { temperature: 0.8 },
    extended_sampling: { top_k: 40 },
    assembly: { use_sysprompt: false, squash_system_messages: false, names_behavior: 0 },
    order: [
      { identifier: "main", enabled: true },
      { identifier: "chatHistory", enabled: true },
      { identifier: "mood-note", enabled: false },
    ],
    prompts: [
      makeItem({ identifier: "main", name: "Main", content: "（测试语料）请回应。" }),
      makeItem({
        identifier: "chatHistory",
        name: "Chat History",
        marker: true,
        marker_source: "滚动窗口历史 + 本次输入",
        content_editable: false,
      }),
      makeItem({
        identifier: "mood-note",
        name: "情绪提醒",
        role: "user",
        injection_position: 1,
        injection_depth: 2,
        injection_order: 50,
      }),
    ],
    memory_injection: { enabled: true, position: "in_chat", depth: 1, role: "system", order: 100 },
    override: {},
    source_format: "chat",
    stripped_keys: [],
    warnings: [],
    unsupported: [],
    ...overrides,
  };
}

describe("joinOrderWithPrompts：顺序表是权威", () => {
  it("按 order 顺序生成行，并带上启用状态", () => {
    const rows = joinOrderWithPrompts(makeDetail());

    expect(rows.map((row) => row.identifier)).toEqual(["main", "chatHistory", "mood-note"]);
    expect(rows.map((row) => row.enabled)).toEqual([true, true, false]);
  });

  it("未出现在顺序表里的条目追加到末尾并视为未启用", () => {
    const detail = makeDetail();
    detail.prompts.push(makeItem({ identifier: "orphan", name: "未入顺序" }));
    // order 里没有 orphan → 后端已判定未启用，界面也必须展示它（否则用户看不见）

    const rows = joinOrderWithPrompts(detail);

    expect(rows.map((row) => row.identifier)).toEqual([
      "main",
      "chatHistory",
      "mood-note",
      "orphan",
    ]);
    expect(rows[rows.length - 1].enabled).toBe(false);
  });

  it("顺序表引用不存在的条目时跳过（防御式）", () => {
    const detail = makeDetail();
    detail.order.push({ identifier: "ghost", enabled: true });

    const rows = joinOrderWithPrompts(detail);

    expect(rows.map((row) => row.identifier)).toEqual(["main", "chatHistory", "mood-note"]);
  });

  it("保留 In-Chat 与占位标记，供界面区分展示", () => {
    const [main, chatHistory, moodNote] = joinOrderWithPrompts(makeDetail());

    expect(main.inChat).toBe(false);
    expect(chatHistory.marker).toBe(true);
    expect(moodNote.inChat).toBe(true);
    expect(moodNote.depth).toBe(2);
    expect(moodNote.order).toBe(50);
  });
});

describe("排序操作", () => {
  const rows = joinOrderWithPrompts(makeDetail());

  it("moveRow 上移/下移，越界时原样返回", () => {
    expect(moveRow(rows, 1, -1).map((row) => row.identifier)).toEqual([
      "chatHistory",
      "main",
      "mood-note",
    ]);
    expect(moveRow(rows, 0, -1)).toBe(rows); // 已在首位
    expect(moveRow(rows, 2, 1)).toBe(rows); // 已在末位
  });

  it("reorderRows 把条目拖到目标位置", () => {
    const moved = reorderRows(rows, 2, 0);

    expect(moved.map((row) => row.identifier)).toEqual(["mood-note", "main", "chatHistory"]);
  });

  it("reorderRows 原地不动时返回同一引用（避免无意义的保存）", () => {
    expect(reorderRows(rows, 1, 1)).toBe(rows);
  });

  it("updateRow 只改目标行", () => {
    const updated = updateRow(rows, "main", { enabled: false });

    expect(updated[0].enabled).toBe(false);
    expect(updated[1]).toBe(rows[1]);
  });
});

describe("补丁构造", () => {
  it("toOrderPatch 同时提交顺序与启用状态（只提交顺序会被后端各自合并）", () => {
    const rows = joinOrderWithPrompts(makeDetail());
    rows[2] = { ...rows[2], enabled: true };

    const patch = toOrderPatch(rows);

    expect(patch.prompt_order).toEqual(["main", "chatHistory", "mood-note"]);
    expect(patch.prompts).toEqual({
      main: { enabled: true },
      chatHistory: { enabled: true },
      "mood-note": { enabled: true },
    });
  });

  it("rowPatch 只包住一个条目", () => {
    expect(rowPatch("main", { content: "新正文" })).toEqual({
      prompts: { main: { content: "新正文" } },
    });
  });
});

describe("mergePatch：防抖累积", () => {
  it("二级合并 prompts，连续改两个条目不会互相覆盖", () => {
    const merged = mergePatch(
      { prompts: { main: { enabled: false } } },
      { prompts: { "mood-note": { content: "新的" } } },
    );

    expect(merged.prompts).toEqual({
      main: { enabled: false },
      "mood-note": { content: "新的" },
    });
  });

  it("同一字段后写覆盖先写", () => {
    const merged = mergePatch(
      { sampling: { temperature: 0.5 } },
      { sampling: { temperature: 0.9 } },
    );

    expect(merged.sampling).toEqual({ temperature: 0.9 });
  });

  it("null 必须保留（表示恢复预设原值，不能被当成空值丢掉）", () => {
    const merged = mergePatch({}, { sampling: { temperature: null } });

    expect(merged.sampling).toEqual({ temperature: null });
    expect(isEmptyPatch(merged)).toBe(false);
  });

  it("prompt_order 以后写的为准", () => {
    const merged = mergePatch(
      { prompt_order: ["a", "b"] },
      { prompt_order: ["b", "a"] },
    );

    expect(merged.prompt_order).toEqual(["b", "a"]);
  });

  it("memory_injection 按字段合并（只改 depth 不会丢掉 position）", () => {
    const merged = mergePatch(
      { memory_injection: { position: "in_prompt" } },
      { memory_injection: { depth: 3 } },
    );

    expect(merged.memory_injection).toEqual({ position: "in_prompt", depth: 3 });
  });

  it("空补丁判定", () => {
    expect(isEmptyPatch(mergePatch({}, {}))).toBe(true);
    expect(isEmptyPatch({ assembly: { names_behavior: 1 } })).toBe(false);
  });
});

describe("token 估算（与后端 estimate_tokens 同口径）", () => {
  it("中文按 0.7 token/字符，英文按 4 字符/token", () => {
    expect(estimateTokens("")).toBe(0);
    expect(estimateTokens("中文四个字")).toBe(4); // 5 * 0.7 = 3.5 → 4（四舍五入）
    expect(estimateTokens("abcd")).toBe(1);
  });

  it("只统计已启用且非占位的条目（占位内容运行时才知道）", () => {
    const rows = joinOrderWithPrompts(makeDetail());

    // main 启用且有正文；chatHistory 是占位；mood-note 未启用
    expect(estimatePresetTokens(rows)).toBe(estimateTokens("（测试语料）请回应。"));
  });
});

describe("展示辅助", () => {
  it("describeRow 区分 In-Chat 与普通条目", () => {
    const [main, chatHistory, moodNote] = joinOrderWithPrompts(makeDetail());

    expect(describeRow(main)).toBe("按顺序注入");
    expect(describeRow(chatHistory)).toBe("占位条目");
    expect(describeRow(moodNote)).toBe("In-Chat · depth 2 · order 50");
  });

  it("只有 normal / regenerate 是受支持的触发类型", () => {
    expect(SUPPORTED_TRIGGERS.has("normal")).toBe(true);
    expect(SUPPORTED_TRIGGERS.has("regenerate")).toBe(true);
    expect(SUPPORTED_TRIGGERS.has("swipe")).toBe(false);
  });

  it("formatImportedAt 对非法时间串返回空", () => {
    expect(formatImportedAt("")).toBe("");
    expect(formatImportedAt("not-a-date")).toBe("");
  });
});
