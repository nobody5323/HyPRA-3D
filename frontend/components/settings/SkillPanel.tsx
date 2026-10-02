"use client";

/**
 * 技能面板：渐进式加载能力的可见面（`AGENTS.md §9.6`）。
 *
 * **与能力中心（插件）刻意分成两块**——它们是两种东西：
 * 插件是「代码能力可插拔」（进程级，有权限声明与 core/builtin 分层），
 * 技能是「方法论可插拔」（提示词级，本质就是一段给模型看的说明）。
 * 混在一个列表里会让人以为技能也能提供 provider、也能被依赖。
 *
 * 清单来自 `/health`（页面已在轮询）；**正文按需拉**——技能正文可能上万 token，
 * 一屏把所有正文列出来既没必要也不该发生。
 *
 * 空技能库时**仍然渲染**（与插件面板的空则隐藏不同）：技能是文件形态的，
 * 用户要往里放东西，隐藏面板等于把入口藏了。
 *
 * **删除有两种语义**（由后端按来源定，这里只如实说明）：
 * 自建技能删的是用户自己的文件（不可恢复）；内置技能随包分发，运行时永不删改，
 * 所以「删除」= 写用户侧隐藏清单（文件仍在，清单里带 `deleted` 标记），
 * 因此要单列一块「已隐藏」并给出恢复入口——否则用户删掉内置技能后就再也找不回来。
 */

import { useEffect, useState } from "react";

import { AiBriefForm } from "@/components/settings/AiBriefForm";
import {
  ApiError,
  createSkill,
  deleteSkill,
  draftSkill,
  getSkill,
  reloadSkills,
  restoreSkill,
  setSkillEnabled,
} from "@/lib/api/client";
import type { SkillAiDraft, SkillDetail, SkillInfo } from "@/lib/api/types";

const HINT_CLASS = "mt-1 text-[11px] leading-relaxed text-ink-faint";
const FIELD_CLASS =
  "focus-ring w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint disabled:opacity-60";

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : fallback;
}

export function SkillPanel({
  skills,
  onSkillsChanged,
}: {
  skills: SkillInfo[];
  /** 启停 / 重扫后通知页面刷新（技能清单在 `/health` 里） */
  onSkillsChanged?: () => void;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** 正在二次确认删除的技能 id（删除是不可逆动作，不放在一次点击里） */
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  /** AI 编写技能：草稿面板是否展开 + 一句话需求 + 生成结果（可编辑后才保存） */
  const [authorOpen, setAuthorOpen] = useState(false);
  const [brief, setBrief] = useState("");
  const [draft, setDraft] = useState<SkillAiDraft | null>(null);
  const [draftModel, setDraftModel] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [savingDraft, setSavingDraft] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);

  useEffect(() => {
    if (!openId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoadingDetail(true);
    setError(null);

    getSkill(openId)
      .then((next) => {
        if (!cancelled) setDetail(next);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorText(err, "读取技能详情失败"));
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });

    return () => {
      cancelled = true;
    };
  }, [openId]);

  async function handleToggle(skill: SkillInfo, enabled: boolean) {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      await setSkillEnabled(skill.id, enabled);
      setStatus(
        enabled
          ? `已启用「${skill.name}」——模型可在需要时 study 到它。`
          : `已禁用「${skill.name}」——清单里不再出现，模型也取不到正文。`,
      );
      onSkillsChanged?.();
    } catch (err) {
      setError(errorText(err, "切换技能状态失败"));
    } finally {
      setBusy(false);
    }
  }

  async function handleReload() {
    setReloading(true);
    setError(null);
    setStatus(null);
    try {
      const catalog = await reloadSkills();
      // 重扫不会让隐藏的内置技能冒回来（隐藏清单也在状态文件里），所以这里如实报出来
      const hidden = catalog.summary.hidden ?? 0;
      setStatus(
        `已重新扫描：共 ${catalog.summary.total} 个技能，${catalog.summary.enabled} 个启用。` +
          (hidden > 0 ? `（另有 ${hidden} 个已隐藏）` : ""),
      );
      onSkillsChanged?.();
    } catch (err) {
      setError(errorText(err, "重新扫描失败"));
    } finally {
      setReloading(false);
    }
  }

  /** 生成技能草稿（不落盘）；失败原因原样展示——后端已经写了该怎么办。 */
  async function handleDraft() {
    setDrafting(true);
    setDraftError(null);
    setStatus(null);
    try {
      const result = await draftSkill(brief.trim());
      setDraft(result.draft);
      setDraftModel(result.model);
    } catch (err) {
      setDraftError(errorText(err, "生成失败"));
    } finally {
      setDrafting(false);
    }
  }

  /** 保存草稿：写入用户技能目录（id 冲突会被后端拒），然后重扫。 */
  async function handleSaveDraft() {
    if (!draft) return;
    setSavingDraft(true);
    setDraftError(null);
    try {
      const result = await createSkill({
        id: draft.id.trim(),
        name: draft.name.trim(),
        description: draft.description.trim(),
        when_to_use: draft.when_to_use.trim(),
        body: draft.body,
      });
      setStatus(`已保存技能「${result.id}」——默认已启用，模型需要时就能 study 到它。`);
      setDraft(null);
      setBrief("");
      setAuthorOpen(false);
      onSkillsChanged?.();
    } catch (err) {
      setDraftError(errorText(err, "保存技能失败"));
    } finally {
      setSavingDraft(false);
    }
  }

  /** 删除：仅在二次确认后调用（内置=写隐藏清单，自建=删文件，由后端按来源定）。 */
  async function handleDelete(skill: SkillInfo) {
    setPendingDelete(null);
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const result = await deleteSkill(skill.id);
      setStatus(
        result.hidden
          ? `已隐藏「${result.name}」——内置技能的文件仍在，需要时点「恢复」找回。`
          : `已删除「${result.name}」${result.path ? `（${result.path}）` : ""}。`,
      );
      // 展开着的详情要跟着收起：它已经不在清单里了，留着正文只会让人以为还生效
      if (openId === skill.id) setOpenId(null);
      onSkillsChanged?.();
    } catch (err) {
      setError(errorText(err, "删除技能失败"));
    } finally {
      setBusy(false);
    }
  }

  /** 恢复被隐藏的内置技能。 */
  async function handleRestore(skill: SkillInfo) {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      await restoreSkill(skill.id);
      setStatus(`已恢复「${skill.name}」——它重新回到清单，模型需要时就能 study 到。`);
      onSkillsChanged?.();
    } catch (err) {
      setError(errorText(err, "恢复技能失败"));
    } finally {
      setBusy(false);
    }
  }

  // 隐藏项（内置技能删除后的形态）与可见项分区展示：它们不是「还在用的技能」，
  // 混在一列里会让人以为删除没生效
  const visible = skills.filter((skill) => !skill.deleted);
  const hidden = skills.filter((skill) => skill.deleted);
  const enabledCount = visible.filter((skill) => skill.enabled).length;
  return (
    <section
      aria-labelledby="skill-panel-title"
      className="rounded-lg border border-line bg-surface-panel p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="skill-panel-title" className="text-xs font-medium text-ink-muted">
          技能
        </h3>
        <div className="flex items-center gap-2">
          <span className="text-[11px] tabular-nums text-ink-faint">
            {enabledCount}/{visible.length} 启用
          </span>
          <button
            type="button"
            className="focus-ring rounded px-1.5 py-0.5 text-[11px] text-accent-text hover:text-ink disabled:opacity-50"
            aria-expanded={authorOpen}
            onClick={() => setAuthorOpen((open) => !open)}
          >
            {authorOpen ? "收起编写" : "AI 编写技能"}
          </button>
          <button
            type="button"
            className="focus-ring rounded px-1.5 py-0.5 text-[11px] text-ink-faint hover:text-ink-muted disabled:opacity-50"
            disabled={reloading}
            onClick={() => void handleReload()}
          >
            {reloading ? "扫描中…" : "重新扫描"}
          </button>
        </div>
      </div>

      {authorOpen && (
        <div className="mt-2 flex flex-col gap-2">
          <AiBriefForm
            idPrefix="skill-author"
            brief={brief}
            onBriefChange={setBrief}
            onGenerate={() => void handleDraft()}
            busy={drafting}
            disabled={savingDraft}
            label="用一句话说清你想要的能力"
            placeholder="例如：用户被一件小事气得发抖时，先把他拉回来再谈事情"
            actionLabel="AI 生成草稿"
            hint="生成后可以逐项修改，点保存才写入磁盘"
            error={draftError}
          />

          {draft && (
            <div className="flex flex-col gap-2 card p-3">
              <p className="text-[11px] leading-relaxed text-ink-faint">
                草稿来自模型（{draftModel}）。保存后写到{" "}
                <code className="font-mono">backend/data/skills/{draft.id || "<id>"}/SKILL.md</code>
                ，随时可以删掉或改写。
              </p>

              <div className="grid gap-2 sm:grid-cols-2">
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-ink-muted">
                    id <span className="text-ink-faint">（小写字母/数字/连字符）</span>
                  </span>
                  <input
                    value={draft.id}
                    disabled={savingDraft}
                    onChange={(event) => setDraft({ ...draft, id: event.target.value })}
                    className={FIELD_CLASS}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-ink-muted">名称</span>
                  <input
                    value={draft.name}
                    disabled={savingDraft}
                    onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                    className={FIELD_CLASS}
                  />
                </label>
              </div>

              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">一句话说明</span>
                <input
                  value={draft.description}
                  disabled={savingDraft}
                  onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                  className={FIELD_CLASS}
                />
              </label>

              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">
                  何时用 <span className="text-ink-faint">（模型据此判断该不该 study）</span>
                </span>
                <input
                  value={draft.when_to_use}
                  disabled={savingDraft}
                  onChange={(event) => setDraft({ ...draft, when_to_use: event.target.value })}
                  className={FIELD_CLASS}
                />
              </label>

              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">
                  正文（Markdown，只在模型调用 study_skill 时注入）
                </span>
                <textarea
                  value={draft.body}
                  disabled={savingDraft}
                  rows={14}
                  onChange={(event) => setDraft({ ...draft, body: event.target.value })}
                  className={`${FIELD_CLASS} resize-y font-mono text-[11px] leading-relaxed`}
                />
              </label>

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={savingDraft || !draft.id.trim() || !draft.name.trim() || !draft.body.trim()}
                  onClick={() => void handleSaveDraft()}
                  className="focus-ring rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-ink-on transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-ink-faint"
                >
                  {savingDraft ? "保存中…" : "保存技能"}
                </button>
                <button
                  type="button"
                  disabled={savingDraft}
                  onClick={() => {
                    setDraft(null);
                    setDraftError(null);
                  }}
                  className="focus-ring rounded-lg border border-line px-3 py-1.5 text-xs text-ink-muted transition-colors hover:bg-surface-hover disabled:opacity-50"
                >
                  放弃草稿
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {visible.length === 0 && hidden.length === 0 ? (
        <p className={HINT_CLASS}>
          还没有技能。把 <code className="font-mono">SKILL.md</code> 放进{" "}
          <code className="font-mono">backend/skills/&lt;id&gt;/</code> 或{" "}
          <code className="font-mono">backend/data/skills/&lt;id&gt;/</code>，然后点「重新扫描」。
        </p>
      ) : (
        <>
          {visible.length > 0 && (
            <ul className="mt-2 flex flex-col gap-1">
              {visible.map((skill) => {
                const open = openId === skill.id;
                const pending = pendingDelete === skill.id;
                return (
                  <li key={skill.id}>
                    <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                      {/* 启停与展开是两个独立操作：点开关不该顺手把面板展开 */}
                      <label className="flex items-center gap-1.5">
                        <input
                          type="checkbox"
                          className="focus-ring h-3.5 w-3.5 rounded border-line accent-accent"
                          checked={skill.enabled}
                          disabled={busy}
                          onChange={(event) => void handleToggle(skill, event.target.checked)}
                        />
                        <span className={skill.enabled ? "text-ink" : "text-ink-faint"}>
                          {skill.name}
                        </span>
                      </label>
                      <span
                        className={
                          skill.source === "user"
                            ? "rounded bg-accent-soft px-1.5 py-0.5 text-accent-text"
                            : "rounded bg-surface-raised px-1.5 py-0.5 text-ink-muted"
                        }
                      >
                        {skill.source === "user" ? "自建" : "内置"}
                      </span>
                      <button
                        type="button"
                        aria-expanded={open}
                        className="focus-ring ml-auto rounded px-1.5 py-0.5 text-ink-faint hover:text-ink-muted"
                        onClick={() => setOpenId(open ? null : skill.id)}
                      >
                        {open ? "收起" : `${skill.body_chars} 字`}
                      </button>
                      <button
                        type="button"
                        className="focus-ring rounded px-1.5 py-0.5 text-ink-faint hover:text-danger-text disabled:opacity-50"
                        disabled={busy}
                        onClick={() => setPendingDelete(pending ? null : skill.id)}
                      >
                        删除
                      </button>
                    </div>

                    {pending && (
                      // 二次确认：删除是不可逆动作（自建技能的文件会真的消失），
                      // 而且内置/自建的后果不同，所以在这里把差异说清楚
                      <div className="mt-1 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-danger-soft px-2.5 py-1.5 text-[11px]">
                        <span className="text-danger-text">
                          {skill.source === "user"
                            ? "删除后无法恢复——磁盘上的技能文件会被删掉。"
                            : "内置技能不会被真正删掉：只从清单里隐藏，文件仍在，可随时恢复。"}
                        </span>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void handleDelete(skill)}
                          className="focus-ring rounded-md bg-danger px-2.5 py-1 font-medium text-ink-on transition-colors hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          确认删除
                        </button>
                        <button
                          type="button"
                          onClick={() => setPendingDelete(null)}
                          className="focus-ring rounded-md border border-line px-2.5 py-1 text-ink-muted transition-colors hover:bg-surface-hover"
                        >
                          取消
                        </button>
                      </div>
                    )}

                    {open && (
                      <div className="mt-1 flex flex-col gap-1.5 rounded-lg border border-line bg-surface-inset p-2.5">
                        {skill.when_to_use && (
                          <p className="text-[11px] text-ink-muted">
                            适用场景：{skill.when_to_use}
                          </p>
                        )}
                        {loadingDetail && <p className={HINT_CLASS}>读取正文中…</p>}
                        {!loadingDetail && detail && (
                          // Markdown 原样展示：项目不引 markdown 渲染器（零依赖原则），
                          // 而这里的作用是「让人看得到模型会读到什么」，不是排版美观
                          <pre className="max-h-72 overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed text-ink-muted">
                            {detail.body}
                          </pre>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {hidden.length > 0 && (
            // 隐藏区：只收内置技能（自建技能是物理删除，不会再出现在清单里）。
            // 没有这块，用户删掉一个内置技能后就再也找不回来了。
            <div className="mt-2 rounded-lg border border-line bg-surface-inset p-2.5">
              <p className={HINT_CLASS}>
                已隐藏 {hidden.length} 个内置技能（随包分发的文件没被动过，随时可恢复）
              </p>
              <ul className="mt-1 flex flex-col gap-1">
                {hidden.map((skill) => (
                  <li key={skill.id} className="flex flex-wrap items-center gap-1.5 text-[11px]">
                    <span className="text-ink-faint">{skill.name}</span>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void handleRestore(skill)}
                      className="focus-ring rounded px-1.5 py-0.5 text-accent-text hover:text-ink disabled:opacity-50"
                    >
                      恢复
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <p className={HINT_CLASS}>
        清单常驻上下文（几十 token）；正文只在模型调用 <code className="font-mono">study_skill</code>{" "}
        时取回。
      </p>

      {status && (
        <p role="status" className="mt-2 text-[11px] text-success-text">
          {status}
        </p>
      )}

      {error && (
        <p role="alert" className="mt-2 text-[11px] text-danger-text">
          {error}
        </p>
      )}
    </section>
  );
}
