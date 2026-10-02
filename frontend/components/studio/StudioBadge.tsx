"use client";

/**
 * 创作工坊的共用小徽标。
 *
 * 单独一个文件是为了避免循环依赖：StudioPanel 渲染两个 tab 组件，
 * 而 tab 组件都要用这些徽标。
 */

/** 「内置」徽标：包内文件不直接编辑，可复制或从当前工坊删除。 */
export function BuiltinBadge() {
  return (
    <span
      className="rounded-full bg-surface-raised px-2 py-0.5 text-[11px] text-ink-soft ring-1 ring-line"
      title="内置内容不可直接编辑；可复制为我的，或从当前工坊删除"
    >
      内置
    </span>
  );
}

/** 「停用」徽标：条目不参与触发匹配。 */
export function DisabledBadge() {
  return (
    <span className="rounded-full bg-warning-soft px-2 py-0.5 text-[11px] text-warning-text ring-1 ring-warning/30">
      已停用
    </span>
  );
}

/** 归属徽标：`*` 显示「所有角色」，否则显示角色名（找不到就退回 id）。 */
export function ScopeBadge({
  scope,
  scopeAll,
  personaNames,
}: {
  scope: string;
  /** 后端声明的通配值（catalog.scope_all，通常为 "*"） */
  scopeAll: string;
  /** 角色 id → 显示名（用于把 scope 翻译成人话） */
  personaNames: Record<string, string>;
}) {
  const isAll = scope === scopeAll;
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[11px] ring-1 ${
        isAll
          ? "bg-surface-raised text-ink-soft ring-line"
          : "bg-accent-soft text-accent-text ring-accent/30"
      }`}
    >
      {isAll ? "所有角色" : `专属 · ${personaNames[scope] ?? scope}`}
    </span>
  );
}
