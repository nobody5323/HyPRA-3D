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
 */

import { useEffect, useState } from "react";

import { ApiError, getSkill, reloadSkills, setSkillEnabled } from "@/lib/api/client";
import type { SkillDetail, SkillInfo } from "@/lib/api/types";

const HINT_CLASS = "mt-1 text-[11px] leading-relaxed text-ink-faint";

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
      setStatus(
        `已重新扫描：共 ${catalog.summary.total} 个技能，${catalog.summary.enabled} 个启用。`,
      );
      onSkillsChanged?.();
    } catch (err) {
      setError(errorText(err, "重新扫描失败"));
    } finally {
      setReloading(false);
    }
  }

  const enabledCount = skills.filter((skill) => skill.enabled).length;

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
            {enabledCount}/{skills.length} 启用
          </span>
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

      {skills.length === 0 ? (
        <p className={HINT_CLASS}>
          还没有技能。把 <code className="font-mono">SKILL.md</code> 放进{" "}
          <code className="font-mono">backend/skills/&lt;id&gt;/</code> 或{" "}
          <code className="font-mono">backend/data/skills/&lt;id&gt;/</code>，然后点「重新扫描」。
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1">
          {skills.map((skill) => {
            const open = openId === skill.id;
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
                </div>

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
