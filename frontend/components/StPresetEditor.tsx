"use client";

/**
 * ST 预设编辑器：采样参数 + 组装开关 + 条目清单（开关 / 排序 / 展开编辑）+ 记忆注入。
 *
 * 保存策略：**乐观更新 + 防抖提交**（500ms）。
 * - 改本地 state 立即反馈，补丁经 `mergePatch` 累积后一次提交；
 * - 组件卸载前把未提交的改动 flush 出去 —— 否则用户调完就切走会静默丢改动。
 *
 * 合规（AGENTS.md §6）：这里的正文全部来自**用户自己导入的文件**，
 * 只在本机浏览器 ↔ 本地后端之间流转，不进仓库、不进日志。
 *
 * 无障碍：拖拽之外**必须有**键盘可达的 ↑↓ 排序按钮（拖拽在触屏/读屏下不可用）；
 * 开关与输入框都有可见标签；保存状态用 role="status"，失败用 role="alert"。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { patchStPreset } from "@/lib/api";
import {
  EXTENDED_SAMPLING_FIELDS,
  MEMORY_POSITION_OPTIONS,
  ROLE_OPTIONS,
  SAMPLING_FIELDS,
  SUPPORTED_TRIGGERS,
  TRIGGER_LABELS,
  describeRow,
  estimatePresetTokens,
  isEmptyPatch,
  joinOrderWithPrompts,
  mergePatch,
  moveRow,
  reorderRows,
  rowPatch,
  toOrderPatch,
  updateRow,
} from "@/lib/st-preset";
import type { StPromptRow } from "@/lib/st-preset";
import type { StMemoryInjection, StMemoryPosition, StPresetDetail, StPresetPatch } from "@/lib/types";

/** 键盘焦点样式（浅色主题：鼠尾草绿环） */
const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-panel";

/** 输入框通用样式 */
const INPUT_CLASS =
  "w-full rounded-lg border border-line bg-surface-panel px-2 py-1 text-xs text-ink placeholder:text-ink-faint";

const SAVE_DELAY_MS = 500;

type SaveState = "idle" | "saving" | "saved" | "error";
type AssemblyState = StPresetDetail["assembly"];

const SAVE_LABEL: Record<SaveState, string> = {
  idle: "已同步",
  saving: "保存中…",
  saved: "已保存",
  error: "保存失败",
};

const SAVE_CLASS: Record<SaveState, string> = {
  idle: "text-ink-faint",
  saving: "text-ink-soft",
  saved: "text-success-text",
  error: "text-danger-text",
};

export function StPresetEditor({
  presetId,
  detail,
  onSaved,
  disabled = false,
}: {
  presetId: string;
  detail: StPresetDetail;
  /** 保存成功回调（父组件据此刷新清单里的「已编辑」标记） */
  onSaved?: (detail: StPresetDetail) => void;
  disabled?: boolean;
}) {
  const [rows, setRows] = useState<StPromptRow[]>(() => joinOrderWithPrompts(detail));
  const [sampling, setSampling] = useState<Record<string, number | null>>(() => ({
    ...detail.sampling,
  }));
  const [assembly, setAssembly] = useState<AssemblyState>(() => ({ ...detail.assembly }));
  const [memory, setMemory] = useState<StMemoryInjection>(() => ({ ...detail.memory_injection }));
  /** use_sysprompt 开启时用来替换系统条目正文的文本 */
  const [systemPromptOverride, setSystemPromptOverride] = useState(
    detail.system_prompt_override,
  );
  const [expanded, setExpanded] = useState<string | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);

  const pendingRef = useRef<StPresetPatch>({});
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(async () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    const patch = pendingRef.current;
    pendingRef.current = {};
    if (isEmptyPatch(patch)) return;

    setSaveState("saving");
    setSaveError(null);
    try {
      const updated = await patchStPreset(presetId, patch);
      setSaveState("saved");
      onSaved?.(updated);
    } catch (err) {
      // 失败时保留用户输入（不静默回滚），只把错误显示出来
      setSaveState("error");
      setSaveError(err instanceof Error ? err.message : "保存失败");
    }
  }, [presetId, onSaved]);

  // 卸载前排空未提交的改动（切换预设 / 关闭面板都会触发）
  const flushRef = useRef(flush);
  flushRef.current = flush;
  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      void flushRef.current();
    },
    [],
  );

  const queue = useCallback(
    (patch: StPresetPatch) => {
      pendingRef.current = mergePatch(pendingRef.current, patch);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => void flush(), SAVE_DELAY_MS);
    },
    [flush],
  );

  const enabledCount = useMemo(() => rows.filter((row) => row.enabled).length, [rows]);
  const presetTokens = useMemo(() => estimatePresetTokens(rows), [rows]);
  const memoryHint =
    MEMORY_POSITION_OPTIONS.find((option) => option.value === memory.position)?.hint ?? "";

  // ---------- 条目操作 ----------

  const commitRows = useCallback(
    (next: StPromptRow[], patch: StPresetPatch) => {
      setRows(next);
      queue(patch);
    },
    [queue],
  );

  const toggleRow = (row: StPromptRow) => {
    const enabled = !row.enabled;
    commitRows(updateRow(rows, row.identifier, { enabled }), rowPatch(row.identifier, { enabled }));
  };

  const shiftRow = (index: number, delta: number) => {
    const next = moveRow(rows, index, delta);
    if (next === rows) return;
    commitRows(next, toOrderPatch(next));
  };

  const dropAt = (index: number) => {
    const from = dragIndex;
    setDragIndex(null);
    setDragOver(null);
    if (from === null) return;
    const next = reorderRows(rows, from, index);
    if (next === rows) return;
    commitRows(next, toOrderPatch(next));
  };

  const editField = (
    row: StPromptRow,
    localChanges: Partial<StPromptRow>,
    patchFields: Record<string, unknown>,
  ) => {
    commitRows(updateRow(rows, row.identifier, localChanges), rowPatch(row.identifier, patchFields));
  };

  // ---------- 采样 / 组装 / 记忆注入 ----------

  /** null = 删除该覆盖项，回到预设原值 */
  const setSamplingValue = (key: string, value: number | null) => {
    setSampling((prev) => ({ ...prev, [key]: value }));
    queue({ sampling: { [key]: value } });
  };

  const setAssemblyValue = (patchFields: Partial<AssemblyState>) => {
    setAssembly((prev) => ({ ...prev, ...patchFields }));
    queue({ assembly: patchFields as Record<string, unknown> });
  };

  const setOverrideText = (value: string) => {
    setSystemPromptOverride(value);
    queue({ system_prompt_override: value });
  };

  /** 一键启用/停用全部条目（社区大预设动辄 100+ 条，逐个点太痛） */
  const setAllEnabled = (enabled: boolean) => {
    const next = rows.map((row) => ({ ...row, enabled }));
    commitRows(next, toOrderPatch(next));
  };

  const setMemoryValue = (patchFields: Partial<StMemoryInjection>) => {
    setMemory((prev) => ({ ...prev, ...patchFields }));
    queue({ memory_injection: patchFields as Record<string, unknown> });
  };

  const extendedPresent = EXTENDED_SAMPLING_FIELDS.filter(
    (field) => typeof detail.extended_sampling[field.key] === "number",
  );

  return (
    <div className="flex flex-col gap-4">
      {/* ---- 摘要 + 保存状态 ---- */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="text-ink-soft">
          已启用 {enabledCount} / {rows.length} 条 · 预设正文 ≈ {presetTokens} token
          <span className="text-ink-faint">（不含运行时填充的人设 / 世界书 / 历史）</span>
        </span>
        <span className={SAVE_CLASS[saveState]} role={saveState === "error" ? "alert" : "status"}>
          {SAVE_LABEL[saveState]}
          {saveError ? `：${saveError}` : ""}
        </span>
      </div>

      {/* ---- 采样参数 ---- */}
      <section className="flex flex-col gap-2">
        <h4 className="text-xs font-medium text-ink">采样参数</h4>
        <div className="grid gap-2 sm:grid-cols-2">
          {SAMPLING_FIELDS.map((field) => {
            const value = sampling[field.key];
            const overridden = typeof value === "number";
            const current = overridden ? value : field.fallback;
            const inputId = `st-sampling-${field.key}`;
            return (
              <div
                key={field.key}
                className="rounded-xl border border-line bg-surface-inset px-3 py-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <label htmlFor={inputId} className="text-xs text-ink-muted">
                    {field.label}
                    {!overridden && <span className="ml-1 text-ink-faint">未设置</span>}
                  </label>
                  <div className="flex items-center gap-1">
                    <input
                      id={inputId}
                      type="number"
                      min={field.min}
                      max={field.max}
                      step={field.step}
                      value={current}
                      disabled={disabled}
                      onChange={(event) => {
                        const raw = event.target.value;
                        if (raw === "") {
                          setSamplingValue(field.key, null);
                          return;
                        }
                        const parsed = Number(raw);
                        if (!Number.isNaN(parsed)) setSamplingValue(field.key, parsed);
                      }}
                      className={`w-20 rounded-lg border border-line bg-surface-panel px-2 py-1 text-right text-xs text-ink ${FOCUS_RING}`}
                    />
                    {overridden && (
                      <button
                        type="button"
                        disabled={disabled}
                        onClick={() => setSamplingValue(field.key, null)}
                        aria-label={`恢复${field.label}的预设原值`}
                        className={`rounded-lg px-1.5 py-1 text-xs text-ink-soft transition-colors hover:text-ink-muted disabled:opacity-50 ${FOCUS_RING}`}
                      >
                        恢复
                      </button>
                    )}
                  </div>
                </div>
                <input
                  type="range"
                  min={field.min}
                  max={field.max}
                  step={field.step}
                  value={current}
                  disabled={disabled}
                  onChange={(event) => setSamplingValue(field.key, Number(event.target.value))}
                  aria-label={`${field.label} 滑块`}
                  className={`mt-2 w-full accent-accent ${FOCUS_RING}`}
                />
                <p className="mt-1 text-xs text-ink-faint">{field.hint}</p>
              </div>
            );
          })}
        </div>

        {extendedPresent.length > 0 && (
          <div className="rounded-xl border border-warning/30 bg-warning-soft px-3 py-2">
            <p className="text-xs font-medium text-warning-text">
              以下参数已保留在预设里，但当前模型不会生效
            </p>
            <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-warning-text/90">
              {extendedPresent.map((field) => (
                <li key={field.key}>
                  {field.label} = {String(detail.extended_sampling[field.key])}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* ---- 组装开关 ---- */}
      <section className="flex flex-col gap-2">
        <h4 className="text-xs font-medium text-ink">组装开关</h4>
        <label className="flex items-center gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={assembly.squash_system_messages}
            disabled={disabled}
            onChange={(event) => setAssemblyValue({ squash_system_messages: event.target.checked })}
            className={`accent-accent ${FOCUS_RING}`}
          />
          合并相邻的系统消息
        </label>
        <div className="flex items-center gap-2 text-xs text-ink-muted">
          <label htmlFor="st-names-behavior">历史消息角色名前缀</label>
          <select
            id="st-names-behavior"
            value={assembly.names_behavior}
            disabled={disabled}
            onChange={(event) =>
              setAssemblyValue({ names_behavior: Number(event.target.value) })
            }
            className={`w-44 rounded-lg border border-line bg-surface-panel px-2 py-1 text-xs text-ink ${FOCUS_RING}`}
          >
            <option value={0}>不加</option>
            <option value={1}>加「角色名: 」</option>
            <option value={2}>仅群聊加（等同不加）</option>
          </select>
        </div>
        <div>
          <label className="flex items-center gap-2 text-xs text-ink-muted">
            <input
              type="checkbox"
              checked={assembly.use_sysprompt}
              disabled={disabled}
              onChange={(event) => setAssemblyValue({ use_sysprompt: event.target.checked })}
              className={`accent-accent ${FOCUS_RING}`}
            />
            用统一系统指令覆盖预设里标记为「系统提示」的条目
          </label>
          {assembly.use_sysprompt ? (
            <div className="mt-2">
              <label
                htmlFor="st-sysprompt-override"
                className="text-xs text-ink-muted"
              >
                统一系统指令
              </label>
              <textarea
                id="st-sysprompt-override"
                value={systemPromptOverride}
                rows={5}
                disabled={disabled}
                onChange={(event) => setOverrideText(event.target.value)}
                placeholder="留空 = 不替换，按预设原正文发送"
                className={`mt-1 w-full rounded-lg border border-line bg-surface-panel px-2 py-1 font-mono text-xs text-ink placeholder:text-ink-faint ${FOCUS_RING}`}
              />
            </div>
          ) : (
            <p className="mt-1 text-xs text-ink-faint">
              开启后需要填写覆盖文本才会替换；未填写时仍按预设原正文发送。
            </p>
          )}
        </div>
      </section>

      {/* ---- 条目清单 ---- */}
      <section className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-xs font-medium text-ink">提示词条目</h4>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-ink-faint">拖动左侧手柄排序，或用 ↑ ↓ 微调</span>
            <button
              type="button"
              disabled={disabled}
              onClick={() => setAllEnabled(true)}
              className={`rounded-full bg-surface-raised px-2.5 py-0.5 text-xs text-ink-muted ring-1 ring-line transition-colors hover:bg-surface-hover disabled:opacity-40 ${FOCUS_RING}`}
            >
              全部启用
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => setAllEnabled(false)}
              className={`rounded-full px-2.5 py-0.5 text-xs text-ink-soft transition-colors hover:bg-surface-hover disabled:opacity-40 ${FOCUS_RING}`}
            >
              全部关闭
            </button>
          </div>
        </div>
        <ol className="flex flex-col gap-2">
          {rows.map((row, index) => {
            const open = expanded === row.identifier;
            const bodyId = `st-row-body-${row.identifier}`;
            return (
              <li
                key={row.identifier}
                onDragOver={(event) => {
                  if (dragIndex === null) return;
                  event.preventDefault();
                  setDragOver(index);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  dropAt(index);
                }}
                className={`rounded-xl border px-3 py-2 transition-colors ${
                  dragOver === index && dragIndex !== null
                    ? "border-accent bg-accent-soft/50"
                    : "border-line bg-surface-panel"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    title="拖动排序"
                    draggable={!disabled}
                    onDragStart={(event) => {
                      setDragIndex(index);
                      event.dataTransfer.effectAllowed = "move";
                    }}
                    onDragEnd={() => {
                      setDragIndex(null);
                      setDragOver(null);
                    }}
                    className="cursor-grab select-none text-ink-faint"
                  >
                    ⣿
                  </span>
                  <input
                    type="checkbox"
                    checked={row.enabled}
                    disabled={disabled}
                    onChange={() => toggleRow(row)}
                    aria-label={`启用条目 ${row.name}`}
                    className={`accent-accent ${FOCUS_RING}`}
                  />
                  <button
                    type="button"
                    aria-expanded={open}
                    aria-controls={bodyId}
                    onClick={() => setExpanded(open ? null : row.identifier)}
                    className={`flex min-w-0 flex-1 flex-col items-start rounded-lg px-1 py-0.5 text-left ${FOCUS_RING}`}
                  >
                    <span className="truncate text-xs text-ink">{row.name}</span>
                    <span className="truncate text-xs text-ink-soft">
                      {describeRow(row)}
                      {row.marker && row.markerSource ? ` · ${row.markerSource}` : ""}
                    </span>
                  </button>
                  <span className="rounded-full bg-surface-raised px-2 py-0.5 text-xs text-ink-soft ring-1 ring-line">
                    {row.role}
                  </span>
                  <button
                    type="button"
                    onClick={() => shiftRow(index, -1)}
                    disabled={disabled || index === 0}
                    aria-label={`上移 ${row.name}`}
                    className={`rounded-lg px-1.5 py-1 text-xs text-ink-soft transition-colors hover:text-ink disabled:opacity-40 ${FOCUS_RING}`}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    onClick={() => shiftRow(index, 1)}
                    disabled={disabled || index === rows.length - 1}
                    aria-label={`下移 ${row.name}`}
                    className={`rounded-lg px-1.5 py-1 text-xs text-ink-soft transition-colors hover:text-ink disabled:opacity-40 ${FOCUS_RING}`}
                  >
                    ↓
                  </button>
                </div>

                {open && (
                  <div
                    id={bodyId}
                    className="mt-2 flex flex-col gap-2 border-t border-line pt-2"
                  >
                    <label className="flex flex-col gap-1 text-xs text-ink-muted">
                      名称
                      <input
                        type="text"
                        value={row.name}
                        disabled={disabled}
                        onChange={(event) =>
                          editField(row, { name: event.target.value }, { name: event.target.value })
                        }
                        className={`${INPUT_CLASS} ${FOCUS_RING}`}
                      />
                    </label>

                    <div className="flex flex-wrap gap-2">
                      <label className="flex flex-col gap-1 text-xs text-ink-muted">
                        角色
                        <select
                          value={row.role}
                          disabled={disabled}
                          onChange={(event) =>
                            editField(row, { role: event.target.value }, { role: event.target.value })
                          }
                          className={`w-32 rounded-lg border border-line bg-surface-panel px-2 py-1 text-xs text-ink ${FOCUS_RING}`}
                        >
                          {ROLE_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="flex flex-col gap-1 text-xs text-ink-muted">
                        注入位置
                        <select
                          value={row.inChat ? 1 : 0}
                          disabled={disabled}
                          onChange={(event) => {
                            const position = Number(event.target.value);
                            editField(
                              row,
                              { inChat: position === 1 },
                              { injection_position: position },
                            );
                          }}
                          className={`w-44 rounded-lg border border-line bg-surface-panel px-2 py-1 text-xs text-ink ${FOCUS_RING}`}
                        >
                          <option value={0}>按顺序表（Relative）</option>
                          <option value={1}>插入对话历史（In-Chat）</option>
                        </select>
                      </label>
                      {row.inChat && (
                        <>
                          <label className="flex flex-col gap-1 text-xs text-ink-muted">
                            深度 depth
                            <input
                              type="number"
                              min={0}
                              value={row.depth}
                              disabled={disabled}
                              onChange={(event) => {
                                const depth = Math.max(0, Number(event.target.value) || 0);
                                editField(row, { depth }, { injection_depth: depth });
                              }}
                              className={`w-20 rounded-lg border border-line bg-surface-panel px-2 py-1 text-xs text-ink ${FOCUS_RING}`}
                            />
                          </label>
                          <label className="flex flex-col gap-1 text-xs text-ink-muted">
                            组内排序 order
                            <input
                              type="number"
                              min={0}
                              value={row.order}
                              disabled={disabled}
                              onChange={(event) => {
                                const order = Math.max(0, Number(event.target.value) || 0);
                                editField(row, { order }, { injection_order: order });
                              }}
                              className={`w-20 rounded-lg border border-line bg-surface-panel px-2 py-1 text-xs text-ink ${FOCUS_RING}`}
                            />
                          </label>
                        </>
                      )}
                    </div>

                    <div className="flex flex-col gap-1 text-xs text-ink-muted">
                      {row.contentEditable ? (
                        <>
                          <label htmlFor={`st-content-${row.identifier}`}>正文</label>
                          <textarea
                            id={`st-content-${row.identifier}`}
                            value={row.content}
                            rows={5}
                            disabled={disabled}
                            onChange={(event) =>
                              editField(
                                row,
                                { content: event.target.value },
                                { content: event.target.value },
                              )
                            }
                            className={`rounded-lg border border-line bg-surface-panel px-2 py-1 font-mono text-xs text-ink ${FOCUS_RING}`}
                          />
                        </>
                      ) : (
                        <p className="rounded-lg border border-line bg-surface-inset px-2 py-1 text-ink-soft">
                          占位条目：正文由运行时填充
                          {row.markerSource ? `（${row.markerSource}）` : ""}，不可编辑。
                        </p>
                      )}
                    </div>

                    <label className="flex items-center gap-2 text-xs text-ink-muted">
                      <input
                        type="checkbox"
                        checked={row.forbidOverrides}
                        disabled={disabled}
                        onChange={(event) =>
                          editField(
                            row,
                            { forbidOverrides: event.target.checked },
                            { forbid_overrides: event.target.checked },
                          )
                        }
                        className={`accent-accent ${FOCUS_RING}`}
                      />
                      禁止被统一系统指令覆盖
                    </label>

                    <fieldset className="rounded-lg border border-line px-2 py-1.5">
                      <legend className="px-1 text-xs text-ink-muted">
                        生成类型（都不勾选 = 所有类型都发送）
                      </legend>
                      <div className="flex flex-wrap gap-x-3 gap-y-1">
                        {Object.entries(TRIGGER_LABELS).map(([value, label]) => {
                          const supported = SUPPORTED_TRIGGERS.has(value);
                          return (
                            <label
                              key={value}
                              className={`flex items-center gap-1 text-xs ${
                                supported ? "text-ink-muted" : "text-ink-faint"
                              }`}
                            >
                              <input
                                type="checkbox"
                                checked={row.trigger.includes(value)}
                                disabled={disabled}
                                onChange={(event) => {
                                  const trigger = event.target.checked
                                    ? [...row.trigger, value]
                                    : row.trigger.filter((item) => item !== value);
                                  editField(row, { trigger }, { injection_trigger: trigger });
                                }}
                                className={`accent-accent ${FOCUS_RING}`}
                              />
                              {label}
                              {!supported && <span>（本项目无此入口）</span>}
                            </label>
                          );
                        })}
                      </div>
                    </fieldset>
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      </section>

      {/* ---- 记忆注入 ---- */}
      <section className="flex flex-col gap-2">
        <h4 className="text-xs font-medium text-ink">记忆注入</h4>
        <p className="text-xs text-ink-faint">
          HyPRA 的个人记忆 / 情景记忆 / 语义事实在预设布局中的落点（对齐酒馆的扩展注入语义）。
        </p>
        <label className="flex items-center gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={memory.enabled}
            disabled={disabled}
            onChange={(event) => setMemoryValue({ enabled: event.target.checked })}
            className={`accent-accent ${FOCUS_RING}`}
          />
          启用记忆注入
        </label>
        <div className="flex flex-wrap gap-2">
          <label className="flex flex-col gap-1 text-xs text-ink-muted">
            位置
            <select
              value={memory.position}
              disabled={disabled || !memory.enabled}
              onChange={(event) =>
                setMemoryValue({ position: event.target.value as StMemoryPosition })
              }
              className={`w-52 rounded-lg border border-line bg-surface-panel px-2 py-1 text-xs text-ink disabled:opacity-50 ${FOCUS_RING}`}
            >
              {MEMORY_POSITION_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-ink-muted">
            深度 depth
            <input
              type="number"
              min={0}
              value={memory.depth}
              disabled={disabled || !memory.enabled}
              onChange={(event) =>
                setMemoryValue({ depth: Math.max(0, Number(event.target.value) || 0) })
              }
              className={`w-20 rounded-lg border border-line bg-surface-panel px-2 py-1 text-xs text-ink disabled:opacity-50 ${FOCUS_RING}`}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-ink-muted">
            组内排序 order
            <input
              type="number"
              min={0}
              value={memory.order}
              disabled={disabled || !memory.enabled}
              onChange={(event) =>
                setMemoryValue({ order: Math.max(0, Number(event.target.value) || 0) })
              }
              className={`w-20 rounded-lg border border-line bg-surface-panel px-2 py-1 text-xs text-ink disabled:opacity-50 ${FOCUS_RING}`}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-ink-muted">
            角色
            <select
              value={memory.role}
              disabled={disabled || !memory.enabled}
              onChange={(event) => setMemoryValue({ role: event.target.value })}
              className={`w-32 rounded-lg border border-line bg-surface-panel px-2 py-1 text-xs text-ink disabled:opacity-50 ${FOCUS_RING}`}
            >
              {ROLE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        {memoryHint && <p className="text-xs text-ink-faint">{memoryHint}</p>}
      </section>

      {/* ---- 未生效特性 / 警告 ---- */}
      {detail.unsupported.length > 0 && (
        <section className="rounded-xl border border-warning/30 bg-warning-soft px-3 py-2">
          <h4 className="text-xs font-medium text-warning-text">该预设中本项目不会生效的部分</h4>
          <ul className="mt-1 list-disc pl-4 text-xs leading-relaxed text-warning-text/90">
            {detail.unsupported.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </section>
      )}

      {detail.warnings.length > 0 && (
        <section className="rounded-xl border border-line bg-surface-inset px-3 py-2">
          <h4 className="text-xs font-medium text-ink-muted">导入时的兼容处理</h4>
          <ul className="mt-1 list-disc pl-4 text-xs leading-relaxed text-ink-soft">
            {detail.warnings.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </section>
      )}

      {detail.stripped_keys.length > 0 && (
        <p className="rounded-xl border border-line bg-surface-inset px-3 py-2 text-xs text-ink-soft">
          为安全考虑，导入时已剥离且<strong className="font-medium text-ink-muted">未保存</strong>
          这些端点/密钥类字段：{detail.stripped_keys.join("、")}。
          因此导出时不会还原它们（在酒馆里重新选择服务商即可）。
        </p>
      )}
    </div>
  );
}
