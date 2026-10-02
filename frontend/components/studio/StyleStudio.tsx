"use client";

/**
 * 文风工坊：用户自建「怎么说话」的预设（风格指令 + 避免表达 + 示例对话 + 采样参数）。
 *
 * 对应后端 `/chat/studio/styles*`（见 backend/app/api/studio.py）。
 *
 * 为什么文风要单独成一块、而不是塞进角色卡：
 * **人设（是谁）/ 文风（怎么说话）/ 模型适配（这个模型吃哪套）三者正交**。
 * 同一个人设配「简短利落」和配「古典雅致」是两种完全不同的陪伴体验，而用户
 * 往往想对同一个角色反复试。做成角色卡的一个字段，就等于每换一次文风都得复制
 * 一份角色——而角色 id 是记忆隔离命名空间，复制角色等于换一整套记忆。
 *
 * 几条刻意的设计：
 * - **内置不改包文件**：内置文风的表单整体置灰，仍可复制或从当前工坊删除；
 *   删除只写用户侧隐藏清单，不修改包内原创预设。
 * - **采样参数走白名单**：键名与取值区间来自后端 `catalog.sampling_spec`，
 *   界面不硬编码一份数字（漂移了用户看到的就是假提示）。
 *   文风的采样优先级**最高**（会盖过模型预设档），所以一个量级错误会让这个文风
 *   的每一轮对话都不可用——这里宁可挡住。
 * - **示例对话是反 AI 腔最有效的手段**：做成可增删的成对输入，而不是一段自由文本，
 *   免得用户写成「示例：……」这种模型读不懂的格式。
 * - **删除无级联**：文风不承载会话与记忆，删掉它顶多让偏好里的 style_id 失效，
 *   那一轮回落默认档并给出告警。
 */

import { useCallback, useEffect, useState } from "react";

import {
  createStudioStyle,
  deleteStudioStyle,
  duplicateStudioStyle,
  getStudioStyle,
  updateStudioStyle,
} from "@/lib/api/client";
import type {
  StudioCatalog,
  StudioLimits,
  StudioSamplingSpec,
  StudioStyle,
  StyleExample,
} from "@/lib/api/types";

import { BuiltinBadge } from "./StudioBadge";

/** 编辑态表单：采样参数用字符串存（空串 = 不指定，非数字由校验拦下） */
interface StyleForm {
  name: string;
  description: string;
  tags: string;
  style_prompt: string;
  avoid: string;
  examples: StyleExample[];
  sampling: Record<string, string>;
  conflicts_with: string;
}

const EMPTY_FORM: StyleForm = {
  name: "",
  description: "",
  tags: "",
  style_prompt: "",
  avoid: "",
  examples: [{ user: "", assistant: "" }],
  sampling: {},
  conflicts_with: "",
};

const INPUT_CLASS =
  "focus-ring w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint disabled:opacity-60";

/** 采样参数的展示文案（纯前端关注点：后端只管键名与区间） */
const SAMPLING_LABEL: Record<string, { label: string; hint: string }> = {
  temperature: { label: "温度", hint: "越大越发散；陪伴对话常用 0.7~0.9" },
  max_tokens: { label: "最长回复", hint: "单轮回复的 token 上限" },
  top_p: { label: "top_p", hint: "核采样；一般不动，改了就别再改温度" },
  frequency_penalty: { label: "重复惩罚", hint: "越大越不爱重复用词" },
  presence_penalty: { label: "话题惩罚", hint: "越大越愿意换话题" },
};

/** 逗号 / 顿号 / 换行分隔 → 去空去重的列表（与后端的清洗口径一致） */
function splitList(text: string): string[] {
  const result: string[] = [];
  for (const piece of text.split(/[,，、\n]/)) {
    const item = piece.trim();
    if (item && !result.includes(item)) result.push(item);
  }
  return result;
}

/** 多行文本 → 逐行列表（避免表达这类「一条一行」的字段，顿号会误伤标点） */
function splitLines(text: string): string[] {
  return splitList(text.replace(/\n/g, "、"));
}

function toForm(style: StudioStyle): StyleForm {
  const sampling: Record<string, string> = {};
  for (const [key, value] of Object.entries(style.sampling)) {
    sampling[key] = String(value);
  }
  return {
    name: style.name,
    description: style.description,
    tags: style.tags.join("、"),
    style_prompt: style.style_prompt,
    avoid: style.avoid.join("\n"),
    // 至少留一组空示例，否则「新增示例」按钮点下去才有内容，手感很怪
    examples: style.examples.length > 0 ? style.examples : [{ user: "", assistant: "" }],
    sampling,
    conflicts_with: style.conflicts_with.join("、"),
  };
}

/** 提交前的即时校验（返回给用户的一句话原因；null = 通过） */
function validate(
  form: StyleForm,
  limits: StudioLimits,
  spec: StudioSamplingSpec[],
): string | null {
  const name = form.name.trim();
  const prompt = form.style_prompt.trim();
  if (!name) return "文风名称不能为空";
  if (!prompt) return "风格指令不能为空——它才是这份文风的主体";

  const maxName = limits.style_name ?? 40;
  if (name.length > maxName) return `文风名称最多 ${maxName} 字`;
  const maxPrompt = limits.style_prompt ?? 4000;
  if (prompt.length > maxPrompt) {
    return `风格指令最多 ${maxPrompt} 字（当前 ${prompt.length} 字）`;
  }
  const maxDescription = limits.style_description ?? 200;
  if (form.description.trim().length > maxDescription) {
    return `一句话说明最多 ${maxDescription} 字`;
  }

  for (const item of spec) {
    const raw = (form.sampling[item.key] ?? "").trim();
    if (!raw) continue;
    const value = Number(raw);
    if (!Number.isFinite(value)) {
      return `${SAMPLING_LABEL[item.key]?.label ?? item.key} 必须是数字`;
    }
    if (value < item.min || value > item.max) {
      return `${SAMPLING_LABEL[item.key]?.label ?? item.key} 应在 ${item.min} ~ ${item.max} 之间`;
    }
  }

  // 半截示例（只有一边）后端会拒；这里先拦一道，省一次往返
  const half = form.examples.findIndex(
    (item) => item.user.trim().length > 0 !== item.assistant.trim().length > 0,
  );
  if (half >= 0) return `第 ${half + 1} 组示例对话要「用户发言」和「目标回应」都填`;
  return null;
}

function messageOf(exc: unknown, fallback: string): string {
  return exc instanceof Error ? exc.message : fallback;
}

export function StyleStudio({
  catalog,
  disabled = false,
  onCatalog,
}: {
  catalog: StudioCatalog;
  /** 对话进行中时禁止改动（避免与正在跑的对话争用配置） */
  disabled?: boolean;
  /** 写操作成功后回传刷新过的 catalog（页面据此刷新文风选择器） */
  onCatalog: (catalog: StudioCatalog) => void;
}) {
  /** 当前选中的文风 id；null = 尚未选中 */
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<StudioStyle | null>(null);
  const [form, setForm] = useState<StyleForm>(EMPTY_FORM);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  /** 删除二次确认（不用 window.confirm：阻塞式原生弹窗无法承载影响面说明） */
  const [confirmDelete, setConfirmDelete] = useState(false);

  const limits = catalog.limits;
  const spec = catalog.sampling_spec;
  const isBuiltin = detail?.builtin ?? false;
  const locked = isBuiltin || disabled;

  /** 选中并加载详情（清单项不含风格指令，正文要单独取） */
  const select = useCallback(async (styleId: string) => {
    setCreating(false);
    setSelectedId(styleId);
    setConfirmDelete(false);
    setError(null);
    setStatus(null);
    setBusy(true);
    try {
      const style = await getStudioStyle(styleId);
      setDetail(style);
      setForm(toForm(style));
    } catch (exc) {
      setDetail(null);
      setError(messageOf(exc, "读取文风失败"));
    } finally {
      setBusy(false);
    }
  }, []);

  // 首次打开自动选中第一份文风：否则进来是一片空白表单，用户不知道该点哪里
  useEffect(() => {
    if (creating || selectedId || disabled) return;
    const first = catalog.styles[0];
    if (first) void select(first.id);
  }, [catalog.styles, creating, selectedId, disabled, select]);

  function startCreating() {
    setCreating(true);
    setSelectedId(null);
    setDetail(null);
    setForm(EMPTY_FORM);
    setConfirmDelete(false);
    setError(null);
    setStatus(null);
  }

  function update<K extends keyof StyleForm>(key: K, value: StyleForm[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function updateExample(index: number, key: keyof StyleExample, value: string) {
    setForm((prev) => ({
      ...prev,
      examples: prev.examples.map((item, position) =>
        position === index ? { ...item, [key]: value } : item,
      ),
    }));
  }

  function addExample() {
    setForm((prev) => ({
      ...prev,
      examples: [...prev.examples, { user: "", assistant: "" }],
    }));
  }

  function removeExample(index: number) {
    setForm((prev) => ({
      ...prev,
      // 至少留一组：全删光之后界面上就没有「加一组」的落点了
      examples:
        prev.examples.length <= 1
          ? [{ user: "", assistant: "" }]
          : prev.examples.filter((_, position) => position !== index),
    }));
  }

  function updateSampling(key: string, value: string) {
    setForm((prev) => ({ ...prev, sampling: { ...prev.sampling, [key]: value } }));
  }

  /** 表单 → 请求体（空串的采样参数 = 不指定，不带进 payload） */
  function toInput() {
    const sampling: Record<string, number> = {};
    for (const item of spec) {
      const raw = (form.sampling[item.key] ?? "").trim();
      if (raw) sampling[item.key] = Number(raw);
    }
    return {
      name: form.name.trim(),
      description: form.description.trim(),
      tags: splitList(form.tags),
      style_prompt: form.style_prompt.trim(),
      avoid: splitLines(form.avoid),
      examples: form.examples.filter(
        (item) => item.user.trim() && item.assistant.trim(),
      ),
      sampling,
      conflicts_with: splitList(form.conflicts_with),
    };
  }

  async function save() {
    const problem = validate(form, limits, spec);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    setStatus(null);
    const input = toInput();
    try {
      const result = creating
        ? await createStudioStyle(input)
        : await updateStudioStyle(selectedId ?? "", input);
      setDetail(result.style);
      setForm(toForm(result.style));
      setSelectedId(result.style.id);
      onCatalog(result.catalog);
      setStatus(
        creating
          ? `文风已创建（id：${result.style.id}），下一轮对话即可选用`
          : "已保存，下一轮对话即生效",
      );
      setCreating(false);
    } catch (exc) {
      setError(messageOf(exc, "保存失败"));
    } finally {
      setBusy(false);
    }
  }

  async function duplicate() {
    if (!selectedId) return;
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const result = await duplicateStudioStyle(selectedId);
      onCatalog(result.catalog);
      await select(result.style.id);
      setStatus("已复制为我的文风，可直接编辑");
    } catch (exc) {
      setError(messageOf(exc, "复制失败"));
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
      const result = await deleteStudioStyle(selectedId);
      onCatalog(result.catalog);
      setDetail(null);
      setSelectedId(null);
      setForm(EMPTY_FORM);
      setConfirmDelete(false);
      setStatus(`已删除文风「${result.name}」（对话与记忆不受影响）`);
    } catch (exc) {
      setError(messageOf(exc, "删除失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      {/* 左：文风列表 */}
      <div className="flex shrink-0 flex-col gap-2 lg:w-60">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-medium text-ink-muted">
            文风（{catalog.styles.length}）
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
          {catalog.styles.map((item) => {
            const active = !creating && item.id === selectedId;
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => void select(item.id)}
                  aria-current={active ? "true" : undefined}
                  className={`focus-ring w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                    active
                      ? "border-accent/40 bg-accent-soft"
                      : "border-line bg-surface-panel hover:bg-surface-hover"
                  }`}
                >
                  <span className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm text-ink">{item.name}</span>
                    {item.builtin && <BuiltinBadge />}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-ink-soft">
                    {item.description || "未填说明"}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-ink-faint">
                    {item.example_count > 0
                      ? `${item.example_count} 组示例`
                      : "没有示例（反 AI 腔效果会弱很多）"}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      {/* 右：编辑表单 */}
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        {creating && (
          <p className="rounded-lg bg-accent-soft px-3 py-2 text-xs text-accent-text">
            新文风：填好名称与风格指令后保存。文风与人设正交，建好之后可以配任意角色，
            不必复制角色卡（复制角色会换掉它的一整套记忆）。
          </p>
        )}
        {isBuiltin && (
          <p className="rounded-lg bg-surface-raised px-3 py-2 text-xs text-ink-muted">
            这是内置文风，内容不能直接改。可以复制为我的文风，也可以从当前创作工坊删除；
            删除只写用户侧隐藏清单、不改包内资源。
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

        {!detail && !creating ? (
          <p className="text-sm text-ink-soft">选择左侧文风查看与编辑，或点「新建」。</p>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">
                  名称 <span className="text-ink-faint">（必填，显示用）</span>
                </span>
                <input
                  value={form.name}
                  onChange={(event) => update("name", event.target.value)}
                  disabled={locked}
                  className={INPUT_CLASS}
                  placeholder="例如：深夜电台"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">一句话说明</span>
                <input
                  value={form.description}
                  onChange={(event) => update("description", event.target.value)}
                  disabled={locked}
                  className={INPUT_CLASS}
                  placeholder="例如：低音量、慢节奏，像凌晨三点的电台"
                />
              </label>
            </div>

            <label className="flex flex-col gap-1">
              <span className="flex items-center justify-between text-xs text-ink-muted">
                <span>
                  风格指令{" "}
                  <span className="text-ink-faint">
                    （必填：怎么写、节奏如何、说什么不说什么）
                  </span>
                </span>
                <span className="text-[11px] text-ink-faint">
                  {form.style_prompt.length} / {limits.style_prompt ?? 4000}
                </span>
              </span>
              <textarea
                value={form.style_prompt}
                onChange={(event) => update("style_prompt", event.target.value)}
                disabled={locked}
                rows={8}
                className={`${INPUT_CLASS} resize-y leading-relaxed`}
                placeholder="例如：低声说话，句子短，一次只说一两句。先接住对方的情绪，再问一个具体的小问题。"
              />
            </label>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">
                  避免表达 <span className="text-ink-faint">（一条一行，克制使用）</span>
                </span>
                <textarea
                  value={form.avoid}
                  onChange={(event) => update("avoid", event.target.value)}
                  disabled={locked}
                  rows={4}
                  className={`${INPUT_CLASS} resize-y`}
                  placeholder={"感叹号\n连续追问\n我理解你的感受"}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">
                  标签 <span className="text-ink-faint">（顿号或逗号分隔）</span>
                </span>
                <input
                  value={form.tags}
                  onChange={(event) => update("tags", event.target.value)}
                  disabled={locked}
                  className={INPUT_CLASS}
                  placeholder="温柔、慢、留白"
                />
                <span className="mt-2 text-xs text-ink-muted">
                  与人设冲突的特质{" "}
                  <span className="text-ink-faint">
                    （命中时只提示，不阻断）
                  </span>
                </span>
                <input
                  value={form.conflicts_with}
                  onChange={(event) => update("conflicts_with", event.target.value)}
                  disabled={locked}
                  className={INPUT_CLASS}
                  placeholder="聒噪、长篇"
                />
              </label>
            </div>

            {/* ---------- 示例对话（反 AI 腔最有效的手段） ---------- */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="text-xs text-ink-muted">
                  示例对话{" "}
                  <span className="text-ink-faint">
                    （2~3 组最能定住语气；只会示范语气与句长，不会当成用户的真实情况）
                  </span>
                </span>
                <button
                  type="button"
                  onClick={addExample}
                  disabled={locked || form.examples.length >= (limits.style_examples ?? 6)}
                  className="focus-ring rounded-lg border border-line px-2 py-1 text-xs text-ink-muted transition-colors hover:bg-surface-hover disabled:opacity-50"
                >
                  加一组
                </button>
              </div>

              {form.examples.map((example, index) => (
                <div
                  key={index}
                  className="grid gap-2 rounded-lg border border-line bg-surface-panel p-2 sm:grid-cols-2"
                >
                  <label className="flex flex-col gap-1">
                    <span className="text-[11px] text-ink-faint">用户会说</span>
                    <textarea
                      value={example.user}
                      onChange={(event) => updateExample(index, "user", event.target.value)}
                      disabled={locked}
                      rows={2}
                      className={`${INPUT_CLASS} resize-y`}
                      placeholder="我最近总是失眠"
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[11px] text-ink-faint">希望怎么回</span>
                    <textarea
                      value={example.assistant}
                      onChange={(event) =>
                        updateExample(index, "assistant", event.target.value)
                      }
                      disabled={locked}
                      rows={2}
                      className={`${INPUT_CLASS} resize-y`}
                      placeholder="有几天了？"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() => removeExample(index)}
                    disabled={locked}
                    className="focus-ring justify-self-start rounded-lg border border-line px-2 py-0.5 text-[11px] text-ink-muted transition-colors hover:bg-surface-hover disabled:opacity-50"
                  >
                    删掉这组
                  </button>
                </div>
              ))}
            </div>

            {/* ---------- 采样参数（键名与区间来自后端） ---------- */}
            <div className="flex flex-col gap-2">
              <span className="text-xs text-ink-muted">
                建议采样参数{" "}
                <span className="text-ink-faint">
                  （留空 = 不指定，按模型预设档走；这里填了会**盖过**模型预设）
                </span>
              </span>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {spec.map((item) => (
                  <label key={item.key} className="flex flex-col gap-1">
                    <span className="text-[11px] text-ink-faint">
                      {SAMPLING_LABEL[item.key]?.label ?? item.key}
                      <code className="ml-1">{item.key}</code>
                    </span>
                    <input
                      type="number"
                      inputMode="decimal"
                      step="any"
                      min={item.min}
                      max={item.max}
                      value={form.sampling[item.key] ?? ""}
                      onChange={(event) => updateSampling(item.key, event.target.value)}
                      disabled={locked}
                      className={INPUT_CLASS}
                      placeholder={`${item.min} ~ ${item.max}`}
                    />
                    {SAMPLING_LABEL[item.key]?.hint ? (
                      <span className="text-[11px] text-ink-faint">
                        {SAMPLING_LABEL[item.key].hint}
                      </span>
                    ) : null}
                  </label>
                ))}
              </div>
            </div>

            {detail && (
              <p className="text-[11px] text-ink-faint">
                id：<code className="rounded bg-surface-raised px-1">{detail.id}</code>
                （只作文件定位用；改文风不会影响任何角色的记忆）
              </p>
            )}

            <div className="flex flex-wrap items-center gap-2">
              {!isBuiltin && (
                <button
                  type="button"
                  onClick={() => void save()}
                  disabled={disabled || busy}
                  className="focus-ring rounded-lg bg-accent px-4 py-2 text-xs font-medium text-ink-on transition-colors hover:bg-accent-hover disabled:opacity-50"
                >
                  {creating ? "创建文风" : "保存修改"}
                </button>
              )}

              {isBuiltin && (
                <button
                  type="button"
                  onClick={() => void duplicate()}
                  disabled={disabled || busy}
                  className="focus-ring rounded-lg bg-accent px-4 py-2 text-xs font-medium text-ink-on transition-colors hover:bg-accent-hover disabled:opacity-50"
                >
                  复制为我的文风
                </button>
              )}

              {detail && !confirmDelete && (
                <button
                  type="button"
                  onClick={() => setConfirmDelete(true)}
                  disabled={disabled || busy}
                  className="focus-ring rounded-lg border border-danger/40 px-3 py-2 text-xs text-danger-text transition-colors hover:bg-danger-soft disabled:opacity-50"
                >
                  删除文风
                </button>
              )}

              {detail && confirmDelete && (
                <span className="flex flex-wrap items-center gap-2 rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger-text">
                  删除后这份文风不再出现在选择器里；对话与记忆不受影响，可随时重建。确定？
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
            </div>
          </>
        )}
      </div>
    </div>
  );
}
