"use client";

/**
 * 角色工坊：用户自建陪伴角色（人设正文 + 背景故事）的列表与表单。
 *
 * 对应后端 `/chat/studio/personas*`（见 backend/app/api/studio.py）。
 *
 * 几条刻意的设计：
 * - **内置只读**：内置角色的表单整体置灰，只留「复制为我的角色」——要改先复制。
 *   这样包内的原创预设永远不会被界面改写（docker 镜像里它本来就是只读的）。
 * - **id 只显示、不可编辑**：角色 id 同时是记忆隔离命名空间（温层 collection 名
 *   `memory_{id}`、冷层 companion、个人记忆分库），改 id 等于把角色换成一整套空
 *   记忆，所以后端也不接受修改。
 * - **删除只删角色卡**：会话与记忆保留；删除后的提示会说明「保留了什么」，
 *   免得用户以为数据被一并清掉了。
 * - **上限来自后端**（`catalog.limits`）：界面即时校验与后端写入校验共用同一批
 *   数字，不会出现「界面说没超、后端却拒绝」。
 */

import { useCallback, useEffect, useState } from "react";

import {
  createStudioPersona,
  deleteStudioPersona,
  duplicateStudioPersona,
  getStudioPersona,
  updateStudioPersona,
} from "@/lib/api/client";
import type { StudioCatalog, StudioLimits, StudioPersona } from "@/lib/api/types";

import { BuiltinBadge } from "./StudioBadge";

/** 编辑态表单（列表项不含正文，所以统一用这个结构承载编辑中的值） */
interface PersonaForm {
  name: string;
  title: string;
  description: string;
  tags: string;
  background: string;
  prompt: string;
  variables: string;
}

const EMPTY_FORM: PersonaForm = {
  name: "",
  title: "",
  description: "",
  tags: "",
  background: "",
  prompt: "",
  variables: "",
};

const INPUT_CLASS =
  "focus-ring w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint disabled:opacity-60";

/** 逗号 / 顿号 / 换行分隔 → 去空去重的列表（与后端的清洗口径一致） */
function splitList(text: string): string[] {
  const result: string[] = [];
  for (const piece of text.split(/[,，、\n]/)) {
    const item = piece.trim();
    if (item && !result.includes(item)) result.push(item);
  }
  return result;
}

function toForm(persona: StudioPersona): PersonaForm {
  return {
    name: persona.name,
    title: persona.title,
    description: persona.description,
    tags: persona.tags.join("、"),
    background: persona.background,
    prompt: persona.prompt,
    variables: persona.variables.join("、"),
  };
}

/** 提交前的即时校验（返回给用户的一句话原因；null = 通过） */
function validate(form: PersonaForm, limits: StudioLimits): string | null {
  const name = form.name.trim();
  const prompt = form.prompt.trim();
  if (!name) return "角色名不能为空";
  if (!prompt) return "人设正文不能为空";

  const maxName = limits.persona_name ?? 50;
  if (name.length > maxName) return `角色名最多 ${maxName} 字`;
  const maxPrompt = limits.persona_prompt ?? 8000;
  if (prompt.length > maxPrompt) {
    return `人设正文最多 ${maxPrompt} 字（当前 ${prompt.length} 字）`;
  }
  const maxBackground = limits.persona_background ?? 8000;
  if (form.background.trim().length > maxBackground) {
    return `背景故事最多 ${maxBackground} 字（当前 ${form.background.trim().length} 字）`;
  }
  return null;
}

function messageOf(exc: unknown, fallback: string): string {
  return exc instanceof Error ? exc.message : fallback;
}

export function PersonaStudio({
  catalog,
  disabled = false,
  onCatalog,
}: {
  catalog: StudioCatalog;
  /** 对话进行中时禁止改动（避免与正在跑的对话争用配置） */
  disabled?: boolean;
  /** 写操作成功后回传刷新过的 catalog（页面据此更新陪伴对象选择器） */
  onCatalog: (catalog: StudioCatalog) => void;
}) {
  /** 当前选中的角色 id；null = 尚未选中 */
  const [selectedId, setSelectedId] = useState<string | null>(null);
  /** 当前选中角色的详情（含正文）；null = 新建态或加载失败 */
  const [detail, setDetail] = useState<StudioPersona | null>(null);
  const [form, setForm] = useState<PersonaForm>(EMPTY_FORM);
  /** 是否处于「新建」态 */
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  /** 删除二次确认（不用 window.confirm：阻塞式原生弹窗无法承载影响面说明） */
  const [confirmDelete, setConfirmDelete] = useState(false);

  const limits = catalog.limits;
  const isBuiltin = detail?.builtin ?? false;
  const locked = isBuiltin || disabled;

  /** 选中并加载详情（列表项不含正文，正文要单独取） */
  const select = useCallback(async (personaId: string) => {
    setCreating(false);
    setSelectedId(personaId);
    setConfirmDelete(false);
    setError(null);
    setStatus(null);
    setBusy(true);
    try {
      const persona = await getStudioPersona(personaId);
      setDetail(persona);
      setForm(toForm(persona));
    } catch (exc) {
      setDetail(null);
      setError(messageOf(exc, "读取角色失败"));
    } finally {
      setBusy(false);
    }
  }, []);

  // 首次打开自动选中第一个角色：否则进来是一片空白表单，用户不知道该点哪里
  useEffect(() => {
    if (creating || selectedId || disabled) return;
    const first = catalog.personas[0];
    if (first) void select(first.id);
  }, [catalog.personas, creating, selectedId, disabled, select]);

  function startCreating() {
    setCreating(true);
    setSelectedId(null);
    setDetail(null);
    setForm(EMPTY_FORM);
    setConfirmDelete(false);
    setError(null);
    setStatus(null);
  }

  function update<K extends keyof PersonaForm>(key: K, value: PersonaForm[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  /** 把 {{变量}} 追加到人设正文末尾（比让用户手敲花括号可靠） */
  function insertVariable(name: string) {
    setForm((prev) => ({
      ...prev,
      prompt: `${prev.prompt}${prev.prompt && !prev.prompt.endsWith("\n") ? "\n" : ""}{{${name}}}`,
    }));
  }

  async function save() {
    const problem = validate(form, limits);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    setStatus(null);
    const input = {
      name: form.name.trim(),
      title: form.title.trim(),
      description: form.description.trim(),
      background: form.background.trim(),
      prompt: form.prompt.trim(),
      tags: splitList(form.tags),
      variables: splitList(form.variables),
    };
    try {
      const result = creating
        ? await createStudioPersona(input)
        : await updateStudioPersona(selectedId ?? "", input);
      setDetail(result.persona);
      setForm(toForm(result.persona));
      setSelectedId(result.persona.id);
      onCatalog(result.catalog);
      setStatus(creating ? `角色已创建（id：${result.persona.id}）` : "已保存");
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
      const result = await duplicateStudioPersona(selectedId);
      onCatalog(result.catalog);
      await select(result.persona.id);
      setStatus("已复制为我的角色，可直接编辑");
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
      const result = await deleteStudioPersona(selectedId);
      onCatalog(result.catalog);
      setDetail(null);
      setSelectedId(null);
      setForm(EMPTY_FORM);
      setConfirmDelete(false);
      // 把后端算出的影响面念给用户听：删了什么、留了什么
      setStatus(
        `已删除角色卡「${result.name}」；保留 ${result.sessions} 段对话、` +
          `${result.bound_entries} 条专属世界书条目（${result.kept}）`,
      );
    } catch (exc) {
      setError(messageOf(exc, "删除失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      {/* 左：角色列表 */}
      <div className="flex shrink-0 flex-col gap-2 lg:w-60">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-medium text-ink-muted">
            角色（{catalog.personas.length}）
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
          {catalog.personas.map((item) => {
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
                    {item.title || "未填一句话定位"}
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
            新角色：填好角色名与人设正文后保存。id 由系统生成（不可修改），
            对话时会以它作为记忆隔离命名空间。
          </p>
        )}
        {isBuiltin && (
          <p className="rounded-lg bg-surface-raised px-3 py-2 text-xs text-ink-muted">
            这是内置角色，只能查看。想改就点「复制为我的角色」，得到一份可编辑副本。
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
          <p className="text-sm text-ink-soft">选择左侧角色查看与编辑，或点「新建」。</p>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">
                  角色名 <span className="text-ink-faint">（必填，显示用）</span>
                </span>
                <input
                  value={form.name}
                  onChange={(event) => update("name", event.target.value)}
                  disabled={locked}
                  className={INPUT_CLASS}
                  placeholder="例如：苏澄"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">一句话定位</span>
                <input
                  value={form.title}
                  onChange={(event) => update("title", event.target.value)}
                  disabled={locked}
                  className={INPUT_CLASS}
                  placeholder="例如：会倾听的邻家姐姐"
                />
              </label>
            </div>

            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">角色简介</span>
              <input
                value={form.description}
                onChange={(event) => update("description", event.target.value)}
                disabled={locked}
                className={INPUT_CLASS}
                placeholder="一句话说明这个角色是谁（列表里展示）"
              />
            </label>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">
                  标签 <span className="text-ink-faint">（顿号或逗号分隔）</span>
                </span>
                <input
                  value={form.tags}
                  onChange={(event) => update("tags", event.target.value)}
                  disabled={locked}
                  className={INPUT_CLASS}
                  placeholder="温柔、倾听、姐姐"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">
                  状态变量 <span className="text-ink-faint">（声明用到哪些，仅作记录）</span>
                </span>
                <input
                  value={form.variables}
                  onChange={(event) => update("variables", event.target.value)}
                  disabled={locked}
                  className={INPUT_CLASS}
                  placeholder="user_name、current_mood"
                />
              </label>
            </div>

            <label className="flex flex-col gap-1">
              <span className="flex items-center justify-between text-xs text-ink-muted">
                <span>
                  人设正文 <span className="text-ink-faint">（必填：她是谁、怎么说话）</span>
                </span>
                <span className="text-[11px] text-ink-faint">
                  {form.prompt.length} / {limits.persona_prompt ?? 8000}
                </span>
              </span>
              <textarea
                value={form.prompt}
                onChange={(event) => update("prompt", event.target.value)}
                disabled={locked}
                rows={9}
                className={`${INPUT_CLASS} resize-y leading-relaxed`}
                placeholder="你是{{user_name}}的朋友……（可写性格、口吻、边界与禁忌）"
              />
            </label>

            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-ink-faint">插入变量：</span>
              {catalog.state_vars.map((variable) => (
                <button
                  key={variable.name}
                  type="button"
                  onClick={() => insertVariable(variable.name)}
                  disabled={locked}
                  title={`${variable.description}（示例：${variable.example}；缺省：${variable.default}）`}
                  className="focus-ring rounded-full border border-line px-2 py-0.5 text-[11px] text-ink-muted transition-colors hover:bg-surface-hover disabled:opacity-50"
                >
                  {`{{${variable.name}}}`}
                </button>
              ))}
            </div>

            <label className="flex flex-col gap-1">
              <span className="flex items-center justify-between text-xs text-ink-muted">
                <span>
                  背景故事{" "}
                  <span className="text-ink-faint">
                    （她的过往与世界观；每次对话都会带上，排在正文之后）
                  </span>
                </span>
                <span className="text-[11px] text-ink-faint">
                  {form.background.length} / {limits.persona_background ?? 8000}
                </span>
              </span>
              <textarea
                value={form.background}
                onChange={(event) => update("background", event.target.value)}
                disabled={locked}
                rows={5}
                className={`${INPUT_CLASS} resize-y leading-relaxed`}
                placeholder="例如：她在这座城市住了七年，习惯在深夜泡一杯淡茶……"
              />
            </label>

            {detail && (
              <p className="text-[11px] text-ink-faint">
                id：<code className="rounded bg-surface-raised px-1">{detail.id}</code>
                （记忆按它隔离，创建后不可修改）
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
                  {creating ? "创建角色" : "保存修改"}
                </button>
              )}

              {isBuiltin && (
                <button
                  type="button"
                  onClick={() => void duplicate()}
                  disabled={disabled || busy}
                  className="focus-ring rounded-lg bg-accent px-4 py-2 text-xs font-medium text-ink-on transition-colors hover:bg-accent-hover disabled:opacity-50"
                >
                  复制为我的角色
                </button>
              )}

              {!isBuiltin && detail && !confirmDelete && (
                <button
                  type="button"
                  onClick={() => setConfirmDelete(true)}
                  disabled={disabled || busy}
                  className="focus-ring rounded-lg border border-danger/40 px-3 py-2 text-xs text-danger-text transition-colors hover:bg-danger-soft disabled:opacity-50"
                >
                  删除角色卡
                </button>
              )}

              {!isBuiltin && detail && confirmDelete && (
                <span className="flex flex-wrap items-center gap-2 rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger-text">
                  删除后：会话与记忆会保留（本版不清理），仅角色卡消失。确定？
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
