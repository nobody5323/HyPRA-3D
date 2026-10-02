import { useCallback, useEffect, useState } from "react";

import { JailbreakSwitcher } from "@/components/settings/JailbreakSwitcher";
import { PersonaSwitcher } from "@/components/settings/PersonaSwitcher";
import { PresetSwitcher } from "@/components/settings/PresetSwitcher";
import { StyleSwitcher } from "@/components/settings/StyleSwitcher";
import { UserNameField } from "@/components/settings/UserNameField";
import { StPresetPanel } from "@/components/studio/StPresetPanel";
import {
  getChatPreferences,
  getJailbreakPresets,
  getPersonas,
  getPresets,
  getStyles,
  setChatPreferences,
  type ChatPreferences,
} from "@/lib/api/client";
import type {
  JailbreakCatalog,
  PersonaCatalog,
  PresetCatalog,
  StyleCatalog,
} from "@/lib/api/types";
import { DEFAULT_USER_NAME } from "@/lib/chat/user";

/**
 * 人设与文风分组。
 *
 * 这里改的是**共享偏好**（存后端 `data/chat-preferences.json`）：
 * 控制台、Web 端、桌宠窗三者的 localStorage 互不相通（origin 不同），
 * 所以「用哪套提示词」交给后端保管，三处读同一份。
 *
 * 优先级：用户偏好 > 部署声明的默认（`default_persona_id` / `default_style_id`）。
 * 因此在控制台里选好之后，另两端打开时就用它——这是「调试好再启动」那套流程的一环。
 *
 * 边界要说清楚：**正在运行的**对话界面不会被就地改写（它的选择是会话级状态），
 * 下次打开才生效。
 *
 * 陪伴对象可以由宿主**受控**（传 `personaId` + `onPersonaChange`）：
 * 控制台里三处都要用它（人设与文风 / 会话与记忆 / 酒馆记忆导入），
 * 各存一份状态必然漂移，所以由 `ConsoleApp` 统一持有（见 `usePersonaScope`）。
 * 不传时保持原来的自管状态，单独用这个面板也照常工作。
 */
export function PersonaPanel({
  onOpenStudio,
  personaId: controlledPersonaId,
  personaCatalog,
  onPersonaChange,
}: {
  onOpenStudio?: () => void;
  /** 受控的当前陪伴对象 id；不传则由本面板自管 */
  personaId?: string;
  /** 受控的人设清单；不传则由本面板自拉 */
  personaCatalog?: PersonaCatalog | null;
  /** 受控模式下切换陪伴对象的回调 */
  onPersonaChange?: (id: string) => void;
} = {}) {
  const [personas, setPersonas] = useState<PersonaCatalog | null>(null);
  const [styles, setStyles] = useState<StyleCatalog | null>(null);
  const [presets, setPresets] = useState<PresetCatalog | null>(null);
  const [jailbreaks, setJailbreaks] = useState<JailbreakCatalog | null>(null);

  const [personaId, setPersonaId] = useState("");
  const [styleId, setStyleId] = useState("");
  const [presetId, setPresetId] = useState("");
  const [stPresetId, setStPresetId] = useState("");
  /**
   * 叙事框架（破限）选择。空串 = 未设置（跟随部署默认），`"none"` = 用户明确关闭。
   * 两者都原样保留，界面（`JailbreakSwitcher`）自己会把「未设置」解析成生效档位。
   */
  const [jailbreakId, setJailbreakId] = useState("");
  const [userName, setUserName] = useState(DEFAULT_USER_NAME);
  const [status, setStatus] = useState("");

  useEffect(() => {
    let active = true;

    void (async () => {
      const [fetchedCatalog, styleCatalog, presetCatalog, jailbreakCatalog, preferences] =
        await Promise.all([
          getPersonas(),
          getStyles(),
          getPresets(),
          getJailbreakPresets(),
          getChatPreferences(),
        ]);

      if (!active) return;

      setPersonas(fetchedCatalog);
      setStyles(styleCatalog);
      setPresets(presetCatalog);
      setJailbreaks(jailbreakCatalog);

      // 用户偏好优先；没设置过的项回落到部署声明（再没有就留空 = 内置行为）
      setPersonaId(preferences?.persona_id || fetchedCatalog?.default_persona_id || "");
      setStyleId(preferences?.style_id || styleCatalog?.default_style_id || "");
      setPresetId(preferences?.preset_id || presetCatalog?.auto_preset_id || "");
      setStPresetId(preferences?.st_preset_id || "");
      // 叙事框架**不回落到部署默认**：空串本身是有意义的取值（= 跟随部署默认），
      // 把它解析成具体档位会让部署方日后改默认再也传不到这里
      setJailbreakId(preferences?.jailbreak_id || "");
      setUserName(preferences?.user_name || DEFAULT_USER_NAME);

      const stored = Boolean(
        preferences?.persona_id ||
          preferences?.style_id ||
          preferences?.st_preset_id ||
          preferences?.jailbreak_id,
      );
      setStatus(
        stored
          ? "当前显示的是已保存的偏好（用户偏好优先于部署默认）"
          : "还没设置过偏好，显示的是部署默认；改动会保存为偏好",
      );
    })();

    return () => {
      active = false;
    };
  }, []);

  /** 保存一项偏好，并用后端返回的完整值回填（避免本地与后端不一致） */
  const persist = useCallback(async (patch: Partial<ChatPreferences>, label: string) => {
    try {
      const next = await setChatPreferences(patch);

      setPersonaId(next.persona_id || "");
      setStyleId(next.style_id || "");
      setPresetId(next.preset_id || "");
      setStPresetId(next.st_preset_id || "");
      setJailbreakId(next.jailbreak_id || "");
      setStatus(`${label}已保存：Web 端与桌宠窗下次打开时按它生效`);
    } catch (error: unknown) {
      setStatus(`保存失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }, []);

  /** 受控时以宿主为准（控制台三处共用同一份「当前陪伴对象」），否则用自管状态 */
  const activePersonaId = controlledPersonaId ?? personaId;
  const activeCatalog = personaCatalog ?? personas;

  return (
    <div className="flex flex-col gap-4">
      <p className="rounded-xl bg-surface-inset px-4 py-3 text-xs leading-relaxed text-ink-muted">
        这里的改动会<strong className="text-ink">存到后端</strong>，
        Web 端与桌宠窗下次打开时按它生效——三者的 localStorage 互不相通（origin 不同），
        所以「用哪套提示词」由后端统一保管。
        <strong className="text-ink">正在运行的</strong>对话界面不会被就地改写（那是会话级状态）。
      </p>

      {status ? (
        <p role="status" className="rounded-xl bg-surface-raised px-4 py-2 text-[11px] text-ink-muted">
          {status}
        </p>
      ) : null}

      {/*
        快捷入口：AI 补全人设写在**创作工坊的角色卡编辑器**里（那才是人设正文的家，
        Web 端与控制台共用同一套）。这里只把用户送过去，不重复造一份表单。
      */}
      {onOpenStudio ? (
        <div className="flex flex-wrap items-center gap-2 rounded-xl bg-surface-inset px-4 py-3">
          <span className="text-[11px] leading-relaxed text-ink-muted">
            不会写人设？创作工坊里可以用一句话让模型先写一版。
          </span>
          <button
            type="button"
            onClick={onOpenStudio}
            className="focus-ring rounded-lg border border-line px-2.5 py-1 text-[11px] text-accent-text transition-colors hover:bg-surface-hover"
          >
            去创作工坊写人设 →
          </button>
        </div>
      ) : null}

      <PersonaSwitcher
        value={activePersonaId}
        onChange={(id) => {
          // 受控时由宿主统一落库（它同时供着「会话与记忆」「酒馆记忆导入」两处），
          // 这里再 persist 一次就成了两个来源抢着写同一个键
          if (onPersonaChange) {
            onPersonaChange(id);
            return;
          }
          void persist({ persona_id: id }, "人设");
        }}
        catalog={activeCatalog}
      />
      {/*
        称呼不走 persist：它把错误吞进面板顶部的 status，而输入框自己需要
        成功/失败反馈（用户在这里输入的是文字，不是选一项就生效的下拉）。
      */}
      <UserNameField
        idPrefix="console-persona-user"
        value={userName}
        onSave={async (name) => {
          const next = await setChatPreferences({ user_name: name });
          setUserName(next.user_name || DEFAULT_USER_NAME);
        }}
      />
      <StyleSwitcher
        value={styleId}
        onChange={(id) => void persist({ style_id: id }, "文风")}
        catalog={styles}
      />
      {/*
        叙事框架（破限）：**出厂默认关闭**，放在文风之后——它是「要不要让模型
        按虚构叙事框架回应」的开关，比选哪套文风更靠后也更少动。开关状态同样存
        后端偏好，三个界面读同一份。
      */}
      <JailbreakSwitcher
        value={jailbreakId}
        onChange={(id) => void persist({ jailbreak_id: id }, "叙事框架")}
        catalog={jailbreaks}
      />
      <PresetSwitcher
        value={presetId}
        onChange={(id) => void persist({ preset_id: id }, "提示词预设")}
        catalog={presets}
      />
      <StPresetPanel value={stPresetId} onChange={(id) => void persist({ st_preset_id: id }, "酒馆预设")} />
    </div>
  );
}
