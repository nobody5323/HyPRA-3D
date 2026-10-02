"use client";

/**
 * 交互模式切换（`AGENTS.md §8.1`）：桌宠对话 / 酒馆聊天。
 *
 * 为什么需要一个显式开关：模式此前是**隐式**的——由「有没有选酒馆预设」推断，
 * 界面上没有任何地方说明它。后果实测过：用户在桌宠模式下问酒馆设定，酒馆世界书
 * 按设计不参与召回，而他看不到任何线索，只能得出「召回坏了」的结论。
 *
 * 三条刻意的设计：
 *
 * 1. **默认展开**（`open` 初值 `true`）：这是一个会改变召回行为的开关，
 *    藏起来等于没有。收起是留给「已经调好、想省版面」的用户。
 * 2. **两种模式都写清代价**：不是两个光秃秃的选项名，各自带一句「会 / 不会
 *    召回什么」。用户不需要先理解记忆作用域才能做对选择。
 * 3. **切换会开始新对话**（模式是会话级属性）：这句必须写在界面上，
 *    否则「切了一下，对话没了」会被当成 bug。见 `useChatSession.changeMode`。
 * 4. **说清模式与酒馆预设正交**：模式只管召回范围，提示词走哪条路由「酒馆预设」
 *    那一栏单独选——「桌宠模式 + 酒馆预设」是合法组合（用户明确要过），
 *    不写出来用户会以为两者互斥。
 *
 * 展开后不再是覆盖层（不像 `StyleSwitcher` 的 listbox）：模式只有两个选项，
 * 摊开比弹层更好点，也不会被右列的滚动容器裁掉。
 */

import { useState } from "react";

import { MODE_COMPANION, MODE_TAVERN, modeLabel } from "@/lib/chat/mode";

interface ModeOption {
  id: string;
  name: string;
  /** 这个模式会召回什么——比选项名本身重要 */
  detail: string;
}

const MODE_OPTIONS: ModeOption[] = [
  {
    id: MODE_COMPANION,
    name: "桌宠对话",
    detail: "跨会话长期陪伴。酒馆世界书不参与召回。",
  },
  {
    id: MODE_TAVERN,
    name: "酒馆聊天",
    detail: "还原酒馆原生体验。酒馆世界书参与召回。",
  },
];

export function ModeSwitcher({
  value,
  effective,
  onChange,
  disabled,
}: {
  /** 用户选定的模式；空串 = 没选过（由后端按酒馆预设推断） */
  value: string;
  /** 后端上一轮实际生效的模式；空串 = 还没聊过 */
  effective: string;
  onChange: (mode: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(true);

  const selectedName = modeLabel(value);
  const effectiveName = modeLabel(effective);
  // 选了桌宠模式、后端却按酒馆模式跑？只会出现在「没选过」这一种情况，
  // 因为选过就以后端显式模式优先。所以这里只提示「自动判断的结果」。
  const inferred = !selectedName && effectiveName;

  return (
    <section
      aria-labelledby="mode-title"
      className="rounded-lg border border-line bg-surface-panel p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="mode-title" className="text-xs font-medium text-ink-muted">
          交互模式
        </h3>
        <div className="flex items-center gap-2">
          <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[11px] text-accent-text">
            {selectedName || (inferred ? `自动：${effectiveName}` : "未选择")}
          </span>
          <button
            type="button"
            aria-expanded={open}
            aria-controls="mode-options"
            className="focus-ring rounded px-1.5 py-0.5 text-[11px] text-accent-text hover:text-ink"
            onClick={() => setOpen((prev) => !prev)}
          >
            {open ? "收起" : "展开"}
          </button>
        </div>
      </div>

      {open && (
        <div id="mode-options" className="mt-2 flex flex-col gap-2">
          <div className="flex flex-col gap-1" role="radiogroup" aria-label="交互模式">
            {MODE_OPTIONS.map((option) => {
              const selected = option.id === value;
              return (
                <button
                  key={option.id}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  disabled={disabled}
                  onClick={() => {
                    if (selected) return;
                    onChange(option.id);
                  }}
                  className={`focus-ring rounded border px-2.5 py-2 text-left text-[11px] transition-colors disabled:opacity-50 ${
                    selected
                      ? "border-accent/60 bg-accent-soft text-ink"
                      : "border-line text-ink-muted hover:border-accent/40 hover:bg-surface-hover"
                  }`}
                >
                  <span className="flex items-center gap-1.5">
                    <span aria-hidden className="text-accent-text">
                      {selected ? "●" : "○"}
                    </span>
                    <span className="font-medium">{option.name}</span>
                  </span>
                  <span className="mt-1 block leading-relaxed text-ink-soft">
                    {option.detail}
                  </span>
                </button>
              );
            })}
          </div>

          <p className="text-[11px] leading-relaxed text-ink-faint">
            模式只管
            <span className="text-ink-muted">召回范围</span>
            ：提示词走内置分层还是酒馆预设，由上面「酒馆预设」那一栏单独选，
            <span className="text-ink-muted">两种模式都可以用酒馆预设</span>。
            <br />
            {inferred && (
              <>
                未手动选择时，后端按「有没有选酒馆预设」自动判断，本轮实际生效：
                <span className="text-ink-muted">{effectiveName}</span>。
                <br />
              </>
            )}
            切换模式会<span className="text-ink-muted">开始新对话</span>
            （模式是会话级属性，避免同一段对话前后记忆口径不一致）；旧对话仍在
            「历史记录」里。
          </p>
        </div>
      )}
    </section>
  );
}
