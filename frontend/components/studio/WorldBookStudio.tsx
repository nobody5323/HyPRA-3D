"use client";

/**
 * 世界书工坊：按触发条件注入的世界设定条目（关键词 / 正则 / 语义向量三通道）。
 *
 * 对应后端 `/chat/studio/worldbook*`（见 backend/app/api/studio.py）。
 *
 * 三条刻意的设计：
 * - **试触发**：写完触发条件不必回对话里试——输入一句「用户可能会说的话」，
 *   后端用**与写入完全相同的清洗与校验**构造草稿并逐通道回报结论（命中了哪些
 *   关键词、哪个正则、语义相似度与阈值），用户才知道该往哪儿改。
 * - **归属隔离**（scope）：条目可归属到某一个角色，给专属角色写的设定不会串味到
 *   别的角色；`*` 表示对所有角色生效。
 * - **内置条目可停用**：后端把开关写成用户侧的停用偏好（内置 YAML 一字未改），
 *   界面因此既能「停用不想要的设定」，又不会污染包内的原创内容。
 */

import { useCallback, useEffect, useState } from "react";

import {
  createStudioEntry,
  deleteStudioEntry,
  setStudioEntryEnabled,
  testStudioEntry,
  updateStudioEntry,
} from "@/lib/api/client";
import type {
  StudioCatalog,
  StudioEntryTestResult,
  StudioWorldBookEntry,
} from "@/lib/api/types";

import { BuiltinBadge, DisabledBadge, ScopeBadge } from "./StudioBadge";

interface EntryForm {
  title: string;
  content: string;
  scope: string;
  keys: string;
  regex: string;
  vectorText: string;
  vectorThreshold: number;
  priority: number;
  caseSensitive: boolean;
  enabled: boolean;
}

const INPUT_CLASS =
  "focus-ring w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint disabled:opacity-60";

/** 每行一个 → 去空去重的列表（与后端清洗口径一致） */
function splitLines(text: string): string[] {
  const result: string[] = [];
  for (const piece of text.split("\n")) {
    const item = piece.trim();
    if (item && !result.includes(item)) result.push(item);
  }
  return result;
}

function toForm(entry: StudioWorldBookEntry): EntryForm {
  return {
    title: entry.title,
    content: entry.content,
    scope: entry.scope,
    keys: entry.keys.join("\n"),
    regex: entry.regex.join("\n"),
    vectorText: entry.vector_text,
    vectorThreshold: entry.vector_threshold,
    priority: entry.priority,
    caseSensitive: entry.case_sensitive,
    enabled: entry.enabled,
  };
}

function emptyForm(defaultScope: string): EntryForm {
  return {
    title: "",
    content: "",
    scope: defaultScope,
    keys: "",
    regex: "",
    vectorText: "",
    vectorThreshold: 0.7,
    priority: 0,
    caseSensitive: false,
    enabled: true,
  };
}

function messageOf(exc: unknown, fallback: string): string {
  return exc instanceof Error ? exc.message : fallback;
}

/** 试触发结果：逐通道说明「为什么命中 / 为什么没命中」 */
function TestResult({ result }: { result: StudioEntryTestResult }) {
  const channels: string[] = [];
  if (result.keys_hit.length) channels.push(`关键词命中：${result.keys_hit.join("、")}`);
  if (result.regex_hit.length) channels.push(`正则命中：${result.regex_hit.join("　")}`);
  if (result.vector_score !== null) {
    const passed = result.vector_score >= result.vector_threshold;
    channels.push(
      `语义相似度 ${result.vector_score.toFixed(3)}（阈值 ${result.vector_threshold}）→ ${
        passed ? "达标" : "未达标"
      }`,
    );
  }

  return (
    <div
      role="status"
      className={`rounded-lg px-3 py-2 text-xs ${
        result.matched ? "bg-success-soft text-success-text" : "bg-surface-raised text-ink-muted"
      }`}
    >
      <p className="font-medium">{result.matched ? "会触发这条设定" : "不会触发"}</p>
      <ul className="mt-1 flex flex-col gap-0.5">
        {channels.length > 0 ? (
          channels.map((line) => <li key={line}>{line}</li>)
        ) : (
          <li>三个通道都没命中：补关键词、写正则，或填一段「语义触发文本」。</li>
        )}
      </ul>
      {result.matched && result.injected_text && (
        <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-surface-panel/70 px-2 py-1 text-[11px] text-ink-muted">
          {result.injected_text}
        </pre>
      )}
      {result.matched && (
        <p className="mt-1 text-[11px] text-ink-faint">
          注入约占 {result.estimated_tokens} token
        </p>
      )}
      {result.warnings.map((warning) => (
        <p key={warning} className="mt-1 text-[11px] text-warning-text">
          {warning}
        </p>
      ))}
    </div>
  );
}

export function WorldBookStudio({
  catalog,
  disabled = false,
  defaultScope,
  onCatalog,
}: {
  catalog: StudioCatalog;
  /** 对话进行中时禁止改动 */
  disabled?: boolean;
  /** 新建条目的默认归属（通常是当前对话角色；`*` = 所有角色） */
  defaultScope?: string;
  /** 写操作成功后回传刷新过的 catalog */
  onCatalog: (catalog: StudioCatalog) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<EntryForm>(() =>
    emptyForm(defaultScope ?? catalog.scope_all),
  );
  /** 当前选中条目（内置 = 只读，仅可停用） */
  const [current, setCurrent] = useState<StudioWorldBookEntry | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [testText, setTestText] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<StudioEntryTestResult | null>(null);

  const personaNames: Record<string, string> = {};
  for (const persona of catalog.personas) personaNames[persona.id] = persona.name;

  const isBuiltin = current?.builtin ?? false;
  const locked = isBuiltin || disabled;

  const select = useCallback((entry: StudioWorldBookEntry) => {
    setCreating(false);
    setSelectedId(entry.id);
    setCurrent(entry);
    setForm(toForm(entry));
    setConfirmDelete(false);
    setError(null);
    setStatus(null);
    setTestResult(null);
  }, []);

  // 首次打开自动选中第一条，避免进来面对一张空表单
  useEffect(() => {
    if (creating || selectedId || disabled) return;
    const first = catalog.entries[0];
    if (first) select(first);
  }, [catalog.entries, creating, selectedId, disabled, select]);

  function startCreating() {
    setCreating(true);
    setSelectedId(null);
    setCurrent(null);
    setForm(emptyForm(defaultScope ?? catalog.scope_all));
    setConfirmDelete(false);
    setError(null);
    setStatus(null);
    setTestResult(null);
  }

  function update<K extends keyof EntryForm>(key: K, value: EntryForm[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  /** 草稿 → 请求体（保存与试触发共用，保证「试的就是要存的」） */
  function draftInput() {
    return {
      title: form.title.trim(),
      content: form.content.trim(),
      scope: form.scope,
      keys: splitLines(form.keys),
      regex: splitLines(form.regex),
      vector_text: form.vectorText.trim(),
      vector_threshold: form.vectorThreshold,
      case_sensitive: form.caseSensitive,
      priority: form.priority,
    };
  }

  async function save() {
    if (!form.title.trim()) {
      setError("条目标题不能为空");
      return;
    }
    if (!form.content.trim()) {
      setError("条目正文不能为空");
      return;
    }
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const input = { ...draftInput(), enabled: form.enabled };
      const result = creating
        ? await createStudioEntry(input)
        : await updateStudioEntry(selectedId ?? "", input);
      setCurrent(result.entry);
      setSelectedId(result.entry.id);
      setForm(toForm(result.entry));
      onCatalog(result.catalog);
      setStatus(creating ? `条目已创建（id：${result.entry.id}）` : "已保存");
      setCreating(false);
    } catch (exc) {
      setError(messageOf(exc, "保存失败"));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!selectedId) return;
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const result = await deleteStudioEntry(selectedId);
      onCatalog(result.catalog);
      setCurrent(null);
      setSelectedId(null);
      setConfirmDelete(false);
      setStatus(`已删除条目「${result.entry.title}」`);
    } catch (exc) {
      setError(messageOf(exc, "删除失败"));
    } finally {
      setBusy(false);
    }
  }

  async function toggleEnabled(entry: StudioWorldBookEntry) {
    setBusy(true);
    setError(null);
    try {
      const result = await setStudioEntryEnabled(entry.id, !entry.enabled);
      onCatalog(result.catalog);
      if (selectedId === entry.id) {
        setCurrent(result.entry);
        setForm(toForm(result.entry));
      }
      setStatus(`${result.entry.title}：${result.entry.enabled ? "已启用" : "已停用"}`);
    } catch (exc) {
      setError(messageOf(exc, "切换启用状态失败"));
    } finally {
      setBusy(false);
    }
  }

  async function runTest() {
    if (!testText.trim()) return;
    setTesting(true);
    setError(null);
    try {
      const result = await testStudioEntry({ ...draftInput(), text: testText.trim() });
      setTestResult(result);
    } catch (exc) {
      setTestResult(null);
      setError(messageOf(exc, "试触发失败"));
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      {/* 左：条目列表 */}
      <div className="flex shrink-0 flex-col gap-2 lg:w-64">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-medium text-ink-muted">
            条目（{catalog.entries.length}）
          </h3>
          <button
            type="button"
            onClick={startCreating}
            disabled={disabled || busy}
            className="focus-ring rounded-lg border border-line px-2 py-1 text-xs text-ink-muted transition-colors hover:bg-surface-hover disabled:opacity-50"
          >
            新建
          </button>
        </div>

        <ul className="flex flex-col gap-1">
          {catalog.entries.map((entry) => {
            const active = !creating && entry.id === selectedId;
            return (
              <li key={entry.id}>
                <div
                  className={`rounded-lg border px-3 py-2 transition-colors ${
                    active
                      ? "border-accent/40 bg-accent-soft"
                      : "border-line bg-surface-panel hover:bg-surface-hover"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => select(entry)}
                    aria-current={active ? "true" : undefined}
                    className="focus-ring w-full text-left"
                  >
                    <span className="flex flex-wrap items-center gap-1.5">
                      <span className="text-sm text-ink">{entry.title}</span>
                      {entry.builtin && <BuiltinBadge />}
                      {!entry.enabled && <DisabledBadge />}
                    </span>
                    <span className="mt-1 flex flex-wrap items-center gap-1">
                      <ScopeBadge
                        scope={entry.scope}
                        scopeAll={catalog.scope_all}
                        personaNames={personaNames}
                      />
                      <span className="text-[11px] text-ink-faint">
                        {entry.keys.length > 0 && `${entry.keys.length} 关键词`}
                        {entry.regex.length > 0 && ` ${entry.regex.length} 正则`}
                        {entry.vector_text && " 语义"}
                        {entry.priority !== 0 && ` 优先级 ${entry.priority}`}
                      </span>
                    </span>
                  </button>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={entry.enabled}
                    onClick={() => void toggleEnabled(entry)}
                    disabled={disabled || busy}
                    className="focus-ring mt-1 rounded-full border border-line px-2 py-0.5 text-[11px] text-ink-muted transition-colors hover:bg-surface-hover disabled:opacity-50"
                  >
                    {entry.enabled ? "停用" : "启用"}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </div>

      {/* 右：编辑 + 试触发 */}
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        {creating && (
          <p className="rounded-lg bg-accent-soft px-3 py-2 text-xs text-accent-text">
            新条目：填标题与正文，并至少给一个触发条件（关键词 / 正则 / 语义触发文本）。
            写完可以用下面的「试触发」先验证。
          </p>
        )}
        {isBuiltin && (
          <p className="rounded-lg bg-surface-raised px-3 py-2 text-xs text-ink-muted">
            这是内置条目，正文不可改（可用左侧开关停用它）。
          </p>
        )}
        {error && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger-text">
            {error}
          </p>
        )}
        {status && (
          <p role="status" className="rounded-lg bg-success-soft px-3 py-2 text-xs text-success-text">
            {status}
          </p>
        )}

        {!current && !creating ? (
          <p className="text-sm text-ink-soft">选择左侧条目查看与编辑，或点「新建」。</p>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">
                  标题 <span className="text-ink-faint">（注入时的块标题）</span>
                </span>
                <input
                  value={form.title}
                  onChange={(event) => update("title", event.target.value)}
                  disabled={locked}
                  className={INPUT_CLASS}
                  placeholder="例如：深夜倾听模式"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">归属</span>
                <select
                  value={form.scope}
                  onChange={(event) => update("scope", event.target.value)}
                  disabled={locked}
                  className={INPUT_CLASS}
                >
                  <option value={catalog.scope_all}>所有角色</option>
                  {catalog.personas.map((persona) => (
                    <option key={persona.id} value={persona.id}>
                      专属 · {persona.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">
                触发关键词 <span className="text-ink-faint">（每行一个；任一出现即触发）</span>
              </span>
              <textarea
                value={form.keys}
                onChange={(event) => update("keys", event.target.value)}
                disabled={locked}
                rows={3}
                className={`${INPUT_CLASS} resize-y`}
                placeholder={"失眠\n睡不着"}
              />
            </label>

            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">
                触发正则 <span className="text-ink-faint">（每行一个；写错会在保存时被拦下）</span>
              </span>
              <textarea
                value={form.regex}
                onChange={(event) => update("regex", event.target.value)}
                disabled={locked}
                rows={2}
                className={`${INPUT_CLASS} resize-y font-mono text-xs`}
                placeholder={"深夜|凌晨\n睡不着"}
              />
            </label>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">
                  语义触发文本{" "}
                  <span className="text-ink-faint">（关键词盖不到的同义改写交给它）</span>
                </span>
                <textarea
                  value={form.vectorText}
                  onChange={(event) => update("vectorText", event.target.value)}
                  disabled={locked}
                  rows={2}
                  className={`${INPUT_CLASS} resize-y`}
                  placeholder="用户表达入睡困难、夜里清醒这类困扰"
                />
              </label>
              <div className="flex flex-col gap-3">
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-ink-muted">
                    语义阈值 <span className="text-ink-faint">{form.vectorThreshold}</span>
                  </span>
                  <input
                    type="range"
                    min={0.3}
                    max={0.95}
                    step={0.01}
                    value={form.vectorThreshold}
                    onChange={(event) => update("vectorThreshold", Number(event.target.value))}
                    disabled={locked || !form.vectorText.trim()}
                    className="focus-ring w-full"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-ink-muted">
                    优先级 <span className="text-ink-faint">（越大越靠前）</span>
                  </span>
                  <input
                    type="number"
                    value={form.priority}
                    onChange={(event) => update("priority", Number(event.target.value) || 0)}
                    disabled={locked}
                    className={INPUT_CLASS}
                  />
                </label>
              </div>
            </div>

            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">触发后注入的正文</span>
              <textarea
                value={form.content}
                onChange={(event) => update("content", event.target.value)}
                disabled={locked}
                rows={6}
                className={`${INPUT_CLASS} resize-y leading-relaxed`}
                placeholder="命中时会作为设定补充注入到系统提示里"
              />
            </label>

            <div className="flex flex-wrap items-center gap-4">
              <label className="flex items-center gap-2 text-xs text-ink-muted">
                <input
                  type="checkbox"
                  checked={form.caseSensitive}
                  onChange={(event) => update("caseSensitive", event.target.checked)}
                  disabled={locked}
                  className="focus-ring"
                />
                关键词区分大小写
              </label>
              <label className="flex items-center gap-2 text-xs text-ink-muted">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(event) => update("enabled", event.target.checked)}
                  disabled={locked}
                  className="focus-ring"
                />
                参与触发
              </label>
            </div>

            {/* 试触发 */}
            <div className="flex flex-col gap-2 rounded-xl border border-line bg-surface-inset p-3">
              <span className="text-xs text-ink-muted">
                试触发 <span className="text-ink-faint">（用当前表单当草稿，不保存）</span>
              </span>
              <div className="flex gap-2">
                <input
                  value={testText}
                  onChange={(event) => setTestText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void runTest();
                  }}
                  className={INPUT_CLASS}
                  placeholder="例如：我又失眠了"
                  aria-label="试触发的样例文本"
                />
                <button
                  type="button"
                  onClick={() => void runTest()}
                  disabled={testing || !testText.trim()}
                  className="focus-ring shrink-0 rounded-lg border border-line bg-surface-panel px-3 py-2 text-xs text-ink-muted transition-colors hover:bg-surface-hover disabled:opacity-50"
                >
                  {testing ? "判定中…" : "试触发"}
                </button>
              </div>
              {testResult && <TestResult result={testResult} />}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {!isBuiltin && (
                <button
                  type="button"
                  onClick={() => void save()}
                  disabled={disabled || busy}
                  className="focus-ring rounded-lg bg-accent px-4 py-2 text-xs font-medium text-ink-on transition-colors hover:bg-accent-hover disabled:opacity-50"
                >
                  {creating ? "创建条目" : "保存修改"}
                </button>
              )}

              {!isBuiltin && current && !confirmDelete && (
                <button
                  type="button"
                  onClick={() => setConfirmDelete(true)}
                  disabled={disabled || busy}
                  className="focus-ring rounded-lg border border-danger/40 px-3 py-2 text-xs text-danger-text transition-colors hover:bg-danger-soft disabled:opacity-50"
                >
                  删除条目
                </button>
              )}

              {!isBuiltin && current && confirmDelete && (
                <span className="flex flex-wrap items-center gap-2 rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger-text">
                  删除后这条设定不再注入，确定？
                  <button
                    type="button"
                    onClick={() => void remove()}
                    disabled={busy}
                    className="focus-ring rounded-lg bg-danger px-3 py-1 text-xs text-ink-on disabled:opacity-50"
                  >
                    确认删除
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmDelete(false)}
                    className="focus-ring rounded-lg border border-danger/40 px-3 py-1 text-xs"
                  >
                    取消
                  </button>
                </span>
              )}

              {current && (
                <span className="text-[11px] text-ink-faint">
                  id：<code className="rounded bg-surface-raised px-1">{current.id}</code>
                </span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
