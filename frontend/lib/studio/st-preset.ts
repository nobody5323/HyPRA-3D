/**
 * ST 预设面板的**纯逻辑**：字段元信息、条目行模型、排序操作、补丁合并。
 *
 * 拆出来的理由：这些规则（顺序语义、补丁深合并、上限与步长）都要单独测，
 * 混在组件里只能靠渲染测试间接覆盖，得不偿失。组件只负责把这里的结果画出来。
 *
 * 契约依据：docs/st-preset-compat.md（§3 条目字段 / §4 采样 / §7 记忆注入 / §9 覆盖层）。
 */

import type { StMemoryPosition, StPresetDetail, StPresetPatch, StPromptItem } from "@/lib/api/types";

/** 能真正透传给 provider 的采样参数（与后端 `nodes._APPLICABLE_SAMPLING` 一致） */
export interface SamplingField {
  key: string;
  label: string;
  min: number;
  max: number;
  step: number;
  hint: string;
  /** 预设未提供该项时控件的占位值（仅用于交互，不提交） */
  fallback: number;
}

export const SAMPLING_FIELDS: SamplingField[] = [
  {
    key: "temperature",
    label: "温度 temperature",
    min: 0,
    max: 2,
    step: 0.01,
    hint: "越高越随机。陪伴场景 0.7–1.0 更自然",
    fallback: 0.8,
  },
  {
    key: "top_p",
    label: "核采样 top_p",
    min: 0,
    max: 1,
    step: 0.01,
    hint: "与温度二选一调节，1 = 不限制",
    fallback: 1,
  },
  {
    key: "frequency_penalty",
    label: "频率惩罚",
    min: -2,
    max: 2,
    step: 0.01,
    hint: "正值抑制复读（-2 ~ 2）",
    fallback: 0,
  },
  {
    key: "presence_penalty",
    label: "存在惩罚",
    min: -2,
    max: 2,
    step: 0.01,
    hint: "正值鼓励换话题（-2 ~ 2）",
    fallback: 0,
  },
  {
    key: "max_tokens",
    label: "回复长度上限",
    min: 32,
    max: 4096,
    step: 16,
    hint: "单轮回复的最大 token 数",
    fallback: 512,
  },
];

/**
 * 仅保存与展示的扩展参数。
 *
 * 后端当前**不透传**这些参数（国内托管 API 多会拒绝未知字段），
 * 因此界面必须标注「当前模型忽略」——这比不显示更诚实，用户能看见预设原值。
 */
export const EXTENDED_SAMPLING_FIELDS: { key: string; label: string }[] = [
  { key: "top_k", label: "top_k" },
  { key: "top_a", label: "top_a" },
  { key: "min_p", label: "min_p" },
  { key: "repetition_penalty", label: "重复惩罚" },
  { key: "seed", label: "随机种子" },
  { key: "n", label: "候选数 n" },
];

/** 记忆注入位置（默认 in_chat / depth 1，见契约 §7） */
export const MEMORY_POSITION_OPTIONS: {
  value: StMemoryPosition;
  label: string;
  hint: string;
}[] = [
  {
    value: "in_chat",
    label: "插入对话历史（推荐）",
    hint: "与预设在同 depth·同 order 的条目按系统/用户/角色顺序合并",
  },
  { value: "in_prompt", label: "放在提示词末尾", hint: "等价于 ST 的 IN_PROMPT" },
  { value: "world_info_before", label: "并入世界书（前）槽位", hint: "借 worldInfoBefore 位置注入" },
  { value: "world_info_after", label: "并入世界书（后）槽位", hint: "借 worldInfoAfter 位置注入" },
  { value: "off", label: "不注入", hint: "纯预设体验，记忆层不参与本轮" },
];

export const ROLE_OPTIONS: { value: string; label: string }[] = [
  { value: "system", label: "system" },
  { value: "user", label: "user" },
  { value: "assistant", label: "assistant" },
];

/** 生成类型触发；后四种本项目无对应入口，界面需标灰提示 */
export const TRIGGER_LABELS: Record<string, string> = {
  normal: "常规",
  regenerate: "重新生成",
  continue: "续写",
  impersonate: "扮演用户",
  swipe: "重掷",
  quiet: "静默",
};

export const SUPPORTED_TRIGGERS = new Set(["normal", "regenerate"]);

/** 界面用的条目行：顺序表（顺序 + 启用）与条目定义（正文 + 注入参数）的合并视图 */
export interface StPromptRow {
  identifier: string;
  name: string;
  role: string;
  content: string;
  marker: boolean;
  markerSource: string;
  contentEditable: boolean;
  systemPrompt: boolean;
  /** 禁止被 System Prompt 覆盖文本替换（use_sysprompt 开启时才起作用） */
  forbidOverrides: boolean;
  enabled: boolean;
  inChat: boolean;
  depth: number;
  order: number;
  trigger: string[];
}

function toRow(item: StPromptItem, enabled: boolean): StPromptRow {
  return {
    identifier: item.identifier,
    name: item.name,
    role: item.role,
    content: item.content,
    marker: item.marker,
    markerSource: item.marker_source,
    contentEditable: item.content_editable,
    systemPrompt: item.system_prompt,
    forbidOverrides: item.forbid_overrides,
    enabled,
    inChat: item.injection_position === 1,
    depth: item.injection_depth,
    order: item.injection_order,
    trigger: item.injection_trigger,
  };
}

/**
 * 把详情拼成按顺序排列的条目行。
 *
 * 顺序以 `order` 为准（它是用户拖拽后的权威结果）；`prompts` 里未进顺序表的
 * 条目后端已判定为未启用，这里也一并展示在末尾，避免用户看不见它们。
 */
export function joinOrderWithPrompts(detail: StPresetDetail): StPromptRow[] {
  const byId = new Map(detail.prompts.map((item) => [item.identifier, item]));
  const rows: StPromptRow[] = [];
  const seen = new Set<string>();

  for (const entry of detail.order) {
    const item = byId.get(entry.identifier);
    if (!item) continue; // 后端已剔除悬空引用，这里只做防御
    rows.push(toRow(item, entry.enabled));
    seen.add(entry.identifier);
  }
  for (const item of detail.prompts) {
    if (seen.has(item.identifier)) continue;
    rows.push(toRow(item, false));
  }
  return rows;
}

/** 上/下移动一行（键盘可达的排序方式，不依赖拖拽） */
export function moveRow(rows: StPromptRow[], index: number, delta: number): StPromptRow[] {
  const target = index + delta;
  if (index < 0 || index >= rows.length || target < 0 || target >= rows.length) return rows;
  const next = [...rows];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

/** 拖拽排序：把 from 位置的条目移动到 to 位置 */
export function reorderRows(rows: StPromptRow[], from: number, to: number): StPromptRow[] {
  if (from === to || from < 0 || to < 0 || from >= rows.length || to >= rows.length) return rows;
  const next = [...rows];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

/** 局部更新一行（开关、正文、depth 等） */
export function updateRow(
  rows: StPromptRow[],
  identifier: string,
  changes: Partial<StPromptRow>,
): StPromptRow[] {
  return rows.map((row) => (row.identifier === identifier ? { ...row, ...changes } : row));
}

/** 顺序 + 启用状态 → PATCH 片段（顺序表与开关必须一起提交，否则会被后端各自合并） */
export function toOrderPatch(rows: StPromptRow[]): StPresetPatch {
  const prompts: Record<string, Record<string, unknown>> = {};
  for (const row of rows) {
    prompts[row.identifier] = { enabled: row.enabled };
  }
  return { prompt_order: rows.map((row) => row.identifier), prompts };
}

/** 单条目的字段补丁 */
export function rowPatch(identifier: string, fields: Record<string, unknown>): StPresetPatch {
  return { prompts: { [identifier]: fields } };
}

function compact<T extends object>(value: T | undefined): T | undefined {
  if (!value) return undefined;
  return Object.keys(value).length > 0 ? value : undefined;
}

function mergeNested(
  base: Record<string, Record<string, unknown>> | undefined,
  next: Record<string, Record<string, unknown>> | undefined,
): Record<string, Record<string, unknown>> {
  const result: Record<string, Record<string, unknown>> = { ...(base ?? {}) };
  for (const [key, value] of Object.entries(next ?? {})) {
    result[key] = { ...(result[key] ?? {}), ...value };
  }
  return result;
}

/**
 * 合并两个补丁（防抖累积用）。
 *
 * 同一字段后写覆盖先写；`prompts` 按 identifier 二级合并 —— 否则连续调整
 * 两个条目的开关时，前一次改动会被整个对象覆盖掉。
 */
export function mergePatch(base: StPresetPatch, next: StPresetPatch): StPresetPatch {
  const merged: StPresetPatch = {};

  const sampling = compact({ ...base.sampling, ...next.sampling });
  if (sampling) merged.sampling = sampling;

  const prompts = compact(mergeNested(base.prompts, next.prompts));
  if (prompts) merged.prompts = prompts;

  const order = next.prompt_order ?? base.prompt_order;
  if (order) merged.prompt_order = order;

  const memory = compact({ ...base.memory_injection, ...next.memory_injection });
  if (memory) merged.memory_injection = memory;

  const assembly = compact({ ...base.assembly, ...next.assembly });
  if (assembly) merged.assembly = assembly;

  // 系统指令覆盖文本：空串是**合法值**（= 清空），所以用 undefined 判定而不是真值判定
  const overrideText = next.system_prompt_override ?? base.system_prompt_override;
  if (overrideText !== undefined) merged.system_prompt_override = overrideText;

  return merged;
}

/** 是否有实际内容（避免提交空补丁被后端 400 拒绝） */
export function isEmptyPatch(patch: StPresetPatch): boolean {
  return Object.keys(patch).length === 0;
}

/** ISO 时间串 → 本地「年-月-日 时:分」 */
export function formatImportedAt(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** 条目行的副标题（In-Chat 显示 depth/order，避免用户看不出它在哪注入） */
export function describeRow(row: StPromptRow): string {
  if (row.inChat) {
    return `In-Chat · depth ${row.depth} · order ${row.order}`;
  }
  return row.marker ? "占位条目" : "按顺序注入";
}

/**
 * 启发式 token 估算（与后端 `app/prompts/renderer.estimate_tokens` 同口径）。
 *
 * 中文约 0.7 token/字符，其他字符约 4 字符/token。仅用于**预览**，
 * 真实计数取决于模型 tokenizer（后续接入 Qwen tokenizer 后两者可对齐）。
 */
export function estimateTokens(text: string): number {
  let cjk = 0;
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0;
    if (code >= 0x4e00 && code <= 0x9fff) cjk += 1;
  }
  const other = [...text].length - cjk;
  return Math.round(cjk * 0.7 + other / 4);
}

/**
 * 估算「预设自带正文」的 token 量。
 *
 * 只统计已启用且**非占位**条目的正文：占位条目的内容由运行时填充
 * （人设 / 世界书 / 历史…），静态预设无从预估，界面需明说这一点。
 */
export function estimatePresetTokens(rows: StPromptRow[]): number {
  return rows
    .filter((row) => row.enabled && !row.marker)
    .reduce((total, row) => total + estimateTokens(row.content), 0);
}
