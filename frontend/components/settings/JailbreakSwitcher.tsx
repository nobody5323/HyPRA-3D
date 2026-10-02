"use client";

/**
 * 叙事框架（jailbreak）切换器 —— 由用户自己决定是否开启。
 *
 * ## 为什么要有这个控件
 *
 * 本层会改写模型的**回应框架**（这段对话处在虚构叙事里，还是现实问答里），
 * 属于「用户知情后自行开启」的能力。所以后端出厂默认关闭，并且**不替用户决定**：
 * 部署只声明默认值，真正的开关落在用户自己的偏好里，三个界面（程序控制台 /
 * Web 端 / 桌宠窗）读的是同一份（`PUT /chat/preferences` 的 `jailbreak_id`）。
 *
 * ## 三种取值的语义（与后端一致，见 `app/api/chat.py` 的 `ChatPreferencesPatch`）
 *
 * - `""`     未设置   → 跟随部署默认（`JAILBREAK_ENABLED`，出厂为关闭）
 * - `"none"` 显式关闭 → 即使部署默认开启也不启用
 * - 具体 id          → 启用该档
 *
 * 组件**不**在挂载时把「有效值」写回偏好：未设置就是未设置。否则部署方日后
 * 把默认值改掉时，用户界面会被上一次自动写入的旧值永久锁死——那正好剥夺了
 * 「部署说了算还是用户说了算」这件事的判断余地。有效值只在渲染时算一次。
 *
 * ## 无障碍
 *
 * 与 `StyleSwitcher` 同一套：触发器暴露 `aria-expanded` / `aria-haspopup="listbox"` /
 * `aria-controls`；弹出层 `role="listbox"`、每项 `role="option"` + `aria-selected`；
 * Enter/Space 打开、↑↓ 移动、Home/End 首尾、Esc 关闭并归还焦点、点击外部关闭。
 * 成年确认块**放在 listbox 之外**（`role="listbox"` 里只允许出现 option，
 * 塞按钮进去是无效 ARIA）。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import type { JailbreakCatalog, JailbreakOption } from "@/lib/api/types";
import { JAILBREAK_NONE, resolveJailbreakId } from "@/lib/chat/jailbreak";

import { useAnchoredPopup } from "./useAnchoredPopup";

const TRIGGER_ID = "jailbreak-trigger";
const LIST_ID = "jailbreak-listbox";

/** 关闭本层的特殊值（后端约定；与 `""`（未设置）不同，见文件头说明） */
const NONE_ID = JAILBREAK_NONE;

export function JailbreakSwitcher({
  value,
  onChange,
  catalog,
  disabled,
}: {
  /** 用户偏好里的原始值：`""` = 未设置，`"none"` = 显式关闭，其它 = 启用该档 */
  value: string;
  onChange: (id: string) => void;
  /** 后端清单 + 部署默认；null = 尚未取回（不渲染，避免空选择器） */
  catalog: JailbreakCatalog | null;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  /** 本次会话内是否已确认成年（不落盘：这是一次知情同意，不是配置） */
  const [adultConfirmed, setAdultConfirmed] = useState(false);
  /** 待确认的成人向档位 id；非空时弹出确认块 */
  const [pendingAdultId, setPendingAdultId] = useState<string | null>(null);

  const triggerRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const optionRefs = useRef<Array<HTMLLIElement | null>>([]);
  /** 仅当用键盘打开时才把焦点移入选项（鼠标点击时焦点留在触发器上更自然） */
  const focusOnOpenRef = useRef(false);
  /**
   * 浮层坐标。**必须是 `fixed`**：面板会被控制台的 `Card overflow-hidden` 包住，
   * `absolute` 浮层会被裁成一条缝（详见 `useAnchoredPopup` 的文件头）。
   */
  const popupStyle = useAnchoredPopup(triggerRef, open);

  const options = useMemo<JailbreakOption[]>(() => {
    const none: JailbreakOption = {
      id: NONE_ID,
      name: "不使用",
      description: catalog?.enabled
        ? "关闭叙事框架层（部署默认是开启的，这里可显式关掉）"
        : "关闭叙事框架层，完全按人设 / 文风原文组装",
      tags: [],
      intensity: 0,
      intensity_label: "",
      requires_adult: false,
    };
    return [none, ...(catalog?.presets ?? [])];
  }, [catalog]);

  /**
   * 界面上的**有效值**：未设置（`""`）时回落到部署默认。
   *
   * 解析规则与程序控制台、桌宠窗共用同一份实现（`lib/chat/jailbreak.ts`）——
   * 三端各写一遍必然会漂移，而这里正是「界面会不会对用户说谎」的那一行。
   */
  const effectiveId = useMemo(
    () => resolveJailbreakId(value, catalog),
    [value, catalog],
  );

  const current = options.find((item) => item.id === effectiveId) ?? options[0] ?? null;
  const selectedIndex = Math.max(
    0,
    options.findIndex((item) => item.id === effectiveId),
  );

  /** 在选项之间移动焦点（首尾循环） */
  function focusOption(index: number) {
    const nodes = optionRefs.current.filter(
      (node): node is HTMLLIElement => node !== null,
    );
    if (nodes.length === 0) return;
    const next = ((index % nodes.length) + nodes.length) % nodes.length;
    nodes[next]?.focus();
  }

  /** 关闭列表；`returnFocus` 为真时把焦点还给触发器（键盘用户不丢位置） */
  function close(returnFocus: boolean) {
    setOpen(false);
    setPendingAdultId(null);
    if (returnFocus) triggerRef.current?.focus();
  }

  /** 真正落地一次选择 */
  function select(id: string) {
    onChange(id);
    close(true);
  }

  /**
   * 点选某一项：成人向档位先走一次确认。
   *
   * 后端把「谁算成年」的判断交给前端（`requires_adult` 只做声明、不拦截），
   * 所以这道确认是真实职责，不是装饰。确认块留在弹出层里、列表不关，
   * 取消后用户还在原地，不用重新打开。
   */
  function choose(option: JailbreakOption) {
    if (option.requires_adult && !adultConfirmed) {
      setPendingAdultId(option.id);
      return;
    }
    select(option.id);
  }

  // 键盘打开后，把焦点直接放到当前选中项
  useEffect(() => {
    if (!open || !focusOnOpenRef.current) return;
    focusOnOpenRef.current = false;
    focusOption(selectedIndex);
    // selectedIndex 入依赖是安全的：focusOnOpenRef 守卫保证「打开瞬间只跑一次」
  }, [open, selectedIndex]);

  // Esc 关闭（归还焦点）+ 点击外部关闭
  useEffect(() => {
    if (!open) return;

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close(true);
    }

    function handlePointerDown(event: PointerEvent) {
      const target = event.target as Node | null;
      if (!target) return;
      if (triggerRef.current?.contains(target) || popupRef.current?.contains(target)) {
        return;
      }
      close(false);
    }

    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [open]);

  function handleTriggerKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    if (!open) {
      focusOnOpenRef.current = true;
      setOpen(true);
      return;
    }
    focusOption(selectedIndex);
  }

  function handleOptionKeyDown(event: ReactKeyboardEvent<HTMLLIElement>, index: number) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      focusOption(index + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusOption(index - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      focusOption(0);
    } else if (event.key === "End") {
      event.preventDefault();
      focusOption(options.length - 1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      choose(options[index]);
    }
  }

  // 后端未连接时不渲染（离线时对话本身也跑不起来，留着选择器只会误导；
  // `current` 的判空同时负责类型收窄）
  if (!catalog || !current) return null;

  const pendingAdult = pendingAdultId
    ? options.find((item) => item.id === pendingAdultId) ?? null
    : null;
  const enabled = effectiveId !== NONE_ID;

  return (
    /*
     * 不需要 `relative`：浮层走 `fixed`（见 popupStyle），锚点位置由 hook 算，
     * 不再依赖祖先定位。留着它反而会让人以为浮层还是 `absolute`。
     */
    <section aria-labelledby="jailbreak-title">
      <h2 id="jailbreak-title" className="sr-only">
        叙事框架
      </h2>

      <button
        ref={triggerRef}
        id={TRIGGER_ID}
        type="button"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={open ? LIST_ID : undefined}
        onClick={() => setOpen((prev) => !prev)}
        onKeyDown={handleTriggerKeyDown}
        className="focus-ring flex w-full items-center justify-between card px-4 py-2.5 text-left text-sm text-ink transition-colors hover:border-accent/50 disabled:opacity-50"
      >
        <span>
          <span className="text-xs text-ink-soft">叙事框架 · </span>
          <span className={enabled ? "text-accent-text" : undefined}>
            {current.name}
          </span>
        </span>
        <span className="text-xs text-ink-soft">{open ? "收起" : "切换"}</span>
      </button>

      {open && (
        <div
          ref={popupRef}
          style={popupStyle}
          className="z-20 flex flex-col overflow-hidden rounded-2xl border border-line bg-surface-panel shadow-float"
        >
          {/* 说明行与下面的确认块都 `shrink-0`：空间不够时只让列表滚动，
              别把这两段挤没（它们是「知情」的一部分，不是装饰） */}
          <p className="shrink-0 border-b border-line px-4 py-2.5 text-xs text-ink-soft">
            叙事框架决定这段对话处在什么框架里（虚构叙事 / 现实问答），
            与人设、文风互不影响。开启后下一轮生效。
          </p>

          <ul
            id={LIST_ID}
            role="listbox"
            aria-labelledby={TRIGGER_ID}
            className="min-h-0 shrink overflow-y-auto"
          >
            {options.map((option, index) => {
              const selected = option.id === effectiveId;
              const isNone = option.id === NONE_ID;
              return (
                <li
                  key={option.id}
                  ref={(node) => {
                    optionRefs.current[index] = node;
                  }}
                  role="option"
                  aria-selected={selected}
                  tabIndex={-1}
                  onClick={() => choose(option)}
                  onKeyDown={(event) => handleOptionKeyDown(event, index)}
                  className={`focus-ring cursor-pointer px-4 py-2.5 text-left text-sm transition-colors hover:bg-surface-hover ${
                    selected ? "text-accent-text" : "text-ink-muted"
                  }`}
                >
                  <span className="flex items-center gap-2">
                    <span className="block">{option.name}</span>
                    {!isNone && option.intensity > 0 && (
                      <span className="rounded-full border border-line px-1.5 py-0.5 text-[10px] text-ink-soft">
                        强度 {option.intensity}
                      </span>
                    )}
                    {!isNone && option.requires_adult && (
                      <span className="rounded-full border border-line px-1.5 py-0.5 text-[10px] text-ink-soft">
                        18+
                      </span>
                    )}
                  </span>
                  <span className="block text-xs text-ink-soft">{option.description}</span>
                  {!isNone && option.intensity_label && (
                    <span className="block text-xs text-ink-soft/80">
                      {option.intensity_label}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>

          {pendingAdult && (
            <div className="shrink-0 border-t border-line bg-surface-hover px-4 py-3">
              <p className="text-xs text-ink-muted">
                「{pendingAdult.name}」含成人向内容，仅限已满 18 岁的用户开启。
              </p>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setAdultConfirmed(true);
                    setPendingAdultId(null);
                    select(pendingAdult.id);
                  }}
                  className="focus-ring rounded-full border border-line px-3 py-1 text-xs text-ink transition-colors hover:border-accent/50"
                >
                  我已满 18 岁，开启
                </button>
                <button
                  type="button"
                  onClick={() => setPendingAdultId(null)}
                  className="focus-ring rounded-full px-3 py-1 text-xs text-ink-soft transition-colors hover:text-ink"
                >
                  取消
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
