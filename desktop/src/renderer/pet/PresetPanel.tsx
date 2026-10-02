import { useEffect, useState } from "react";

import { getJailbreakPresets, getPresets, getStyles, listStPresets } from "@/lib/api/client";
import type {
  JailbreakCatalog,
  PresetCatalog,
  StPresetCatalog,
  StyleCatalog,
} from "@/lib/api/types";
import { JAILBREAK_NONE, resolveJailbreakId } from "@/lib/chat/jailbreak";

/**
 * 桌宠窗的「预设」分组：文风 / 提示词预设 / 酒馆预设 / 叙事框架。
 *
 * 为什么桌宠窗要有自己的入口：桌宠窗是**长期开着**的那一个窗口，而这几项原先
 * 只能在 Web 端或程序控制台里改。用户在这里聊天时想换个语气，得先开控制台、
 * 改完再回来——而回来之后还得等窗口重读偏好（见 `useChatSession` 的跨界面同步）。
 * 入口开在这里，「换语气」就是当场一次下拉的事。
 *
 * 三条约束（与 `SessionPanel` 同一套理由）：
 * - **样式不能借 Web 端的组件**：桌宠入口不加载 Tailwind（`pet/main.tsx` 只引
 *   `pet.css`），引 `@/components/*` 会渲染成没有样式的裸元素；那条边界由
 *   `desktop/tests/pet-renderer-boundary.test.ts` 守着。所以这里用桌宠自己的
 *   `.petField` / `.petHint` 体系，尺寸跟着 `--pet-scale` 走。
 * - **清单本面板自己拉**：这几份清单都不长（几十条），而且打开设置面板才会挂载，
 *   拉一次的成本可以忽略；反而比让 App 一直持有几份状态更省事。
 * - **空清单不渲染**：后端离线时清单都是 null，留一排空下拉只会误导。
 */

/** 桌宠窗要用到的预设能力（从 App 传一个最小集合，不透传整个 session 对象） */
export interface PetPresetSettings {
  /** 当前文风 id；`"none"` = 显式关闭文风层，`""` = 没选过（由后端用默认档） */
  styleId: string;
  selectStyle(id: string): void;
  /** 当前提示词（模型）预设 id；`""` = 自动（后端按模型名匹配） */
  presetId: string;
  selectPreset(id: string): void;
  /** 当前酒馆预设 id；`""` = 不使用（走内置分层组装） */
  stPresetId: string;
  selectStPreset(id: string): void;
  /**
   * 叙事框架（破限）选择；`""` = 没选过（跟随部署默认），`"none"` = 明确关闭。
   * 下拉的**显示值**要经 `resolveJailbreakId` 解析，不能直接用这个原始值。
   */
  jailbreakId: string;
  selectJailbreak(id: string): void;
  /** 对话进行中：切换下拉置灰（与角色 / 会话选择器同一口径） */
  busy: boolean;
}

/** 关闭文风层的特殊值（后端约定，见 docs/st-preset-compat.md §10） */
const STYLE_NONE = "none";

export function PresetPanel({
  styleId,
  selectStyle,
  presetId,
  selectPreset,
  stPresetId,
  selectStPreset,
  jailbreakId,
  selectJailbreak,
  busy,
}: PetPresetSettings) {
  const [styles, setStyles] = useState<StyleCatalog | null>(null);
  const [presets, setPresets] = useState<PresetCatalog | null>(null);
  const [stPresets, setStPresets] = useState<StPresetCatalog | null>(null);
  const [jailbreaks, setJailbreaks] = useState<JailbreakCatalog | null>(null);

  // 挂载即拉（设置面板只在打开时挂载，所以「每次打开都是新的一份」）
  useEffect(() => {
    let active = true;

    void (async () => {
      const [styleCatalog, presetCatalog, stCatalog, jailbreakCatalog] =
        await Promise.all([
          getStyles(),
          getPresets(),
          listStPresets(),
          getJailbreakPresets(),
        ]);
      if (!active) return;
      setStyles(styleCatalog);
      setPresets(presetCatalog);
      setStPresets(stCatalog);
      setJailbreaks(jailbreakCatalog);
    })();

    return () => {
      active = false;
    };
  }, []);

  const styleOptions = styles?.styles ?? [];
  const presetOptions = presets?.presets ?? [];
  const stOptions = stPresets?.presets ?? [];
  const jailbreakOptions = jailbreaks?.presets ?? [];

  // 后端离线 / 清单都空：整个分组不渲染（对话本身也跑不起来，留着只会误导）
  if (
    styleOptions.length === 0 &&
    presetOptions.length === 0 &&
    stOptions.length === 0 &&
    jailbreakOptions.length === 0
  ) {
    return null;
  }

  const autoLabel =
    presets?.presets.find((item) => item.id === presets.auto_preset_id)?.label ??
    presets?.auto_preset_id ??
    "";

  /*
   * 叙事框架：下拉的 value 用**解析后的有效值**，不是偏好里的原始值。
   *
   * 「未设置」（空串）的真实含义是「跟随部署默认」，而部署默认可能是开启的。
   * 直接把空串塞给 `value`，下拉会落到第一个 option（「不使用」）上——部署方
   * 明明开着，界面却显示关着，用户下次想关也关不掉（他以为本来就是关的）。
   */
  const effectiveJailbreakId = resolveJailbreakId(jailbreakId, jailbreaks);
  const activeJailbreak = jailbreakOptions.find(
    (item) => item.id === effectiveJailbreakId,
  );

  return (
    <>
      {styleOptions.length > 0 ? (
        <>
          <label className="petField">
            <span>文风</span>
            <select
              value={styleId}
              disabled={busy}
              onChange={(event) => selectStyle(event.target.value)}
            >
              {/*
                「不使用文风」是后端认得的特殊值（`none`），不是空值：
                文风预设的采样参数优先级最高，会盖过酒馆预设作者自己的采样意图，
                导入酒馆预设的用户要「纯预设体验」时必须能关掉这一层。
              */}
              <option value={STYLE_NONE}>不使用文风</option>
              {styleOptions.map((style) => (
                <option key={style.id} value={style.id}>
                  {style.name}
                </option>
              ))}
            </select>
          </label>
          <p className="petHint">
            {styleId === STYLE_NONE
              ? "已关闭文风层：完全按人设原文组装，不追加任何语气约束。"
              : styleOptions.find((style) => style.id === styleId)?.description ||
                "换一份文风就换一种说话方式；下一轮对话即生效。"}
          </p>
        </>
      ) : null}

      {presetOptions.length > 0 ? (
        <label className="petField">
          <span>提示词预设</span>
          <select
            value={presetId}
            disabled={busy}
            onChange={(event) => selectPreset(event.target.value)}
          >
            <option value="">
              {autoLabel ? `自动（按模型匹配：${autoLabel}）` : "自动（按模型匹配）"}
            </option>
            {presetOptions.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {stOptions.length > 0 ? (
        <>
          <label className="petField">
            <span>酒馆预设</span>
            <select
              value={stPresetId}
              disabled={busy}
              onChange={(event) => selectStPreset(event.target.value)}
            >
              <option value="">不使用（走内置分层组装）</option>
              {stOptions.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.name}
                </option>
              ))}
            </select>
          </label>
          <p className="petHint">
            选了酒馆预设即按它的条目顺序组装提示词；世界书与记忆作为扩展注入参与合并。
          </p>
        </>
      ) : null}

      {jailbreakOptions.length > 0 ? (
        <>
          <label className="petField">
            <span>叙事框架</span>
            <select
              value={effectiveJailbreakId}
              disabled={busy}
              onChange={(event) => selectJailbreak(event.target.value)}
            >
              {/*
                「不使用」就是后端认得的 `none`，与「未设置」（空串）不同：
                前者是用户明确关掉，后者是「跟随部署默认」。所以这里不提供
                「跟随部署默认」这一项——它已经通过 `effectiveJailbreakId`
                体现在当前选中项上了，再列一项只会让人分不清两者。
              */}
              <option value={JAILBREAK_NONE}>不使用</option>
              {jailbreakOptions.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {/* 成人向档位只做声明：桌宠窗是个常驻小窗，塞不下确认流程；
                      真正的「确认成年」在 Web 端 / 程序控制台的切换器里做 */}
                  {preset.requires_adult ? `${preset.name}（18+）` : preset.name}
                </option>
              ))}
            </select>
          </label>
          <p className="petHint">
            {effectiveJailbreakId === JAILBREAK_NONE
              ? "已关闭叙事框架层：完全按人设 / 文风原文组装。"
              : activeJailbreak?.description ||
                "决定这段对话处在什么框架里（虚构叙事 / 现实问答）；下一轮生效。"}
          </p>
        </>
      ) : null}

      <p className="petHint">
        这几项与 Web 端、程序控制台共用同一份偏好：在任一处改完，另一处会自行跟上。
      </p>
    </>
  );
}
