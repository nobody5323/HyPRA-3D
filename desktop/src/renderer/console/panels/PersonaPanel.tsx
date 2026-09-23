import { useCallback, useEffect, useState } from "react";

import { PersonaSwitcher } from "@/components/settings/PersonaSwitcher";
import { PresetSwitcher } from "@/components/settings/PresetSwitcher";
import { StyleSwitcher } from "@/components/settings/StyleSwitcher";
import { StPresetPanel } from "@/components/studio/StPresetPanel";
import {
  getChatPreferences,
  getPersonas,
  getPresets,
  getStyles,
  setChatPreferences,
  type ChatPreferences,
} from "@/lib/api/client";
import type { PersonaCatalog, PresetCatalog, StyleCatalog } from "@/lib/api/types";

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
 */
export function PersonaPanel() {
  const [personas, setPersonas] = useState<PersonaCatalog | null>(null);
  const [styles, setStyles] = useState<StyleCatalog | null>(null);
  const [presets, setPresets] = useState<PresetCatalog | null>(null);

  const [personaId, setPersonaId] = useState("");
  const [styleId, setStyleId] = useState("");
  const [presetId, setPresetId] = useState("");
  const [stPresetId, setStPresetId] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    let active = true;

    void (async () => {
      const [personaCatalog, styleCatalog, presetCatalog, preferences] = await Promise.all([
        getPersonas(),
        getStyles(),
        getPresets(),
        getChatPreferences(),
      ]);

      if (!active) return;

      setPersonas(personaCatalog);
      setStyles(styleCatalog);
      setPresets(presetCatalog);

      // 用户偏好优先；没设置过的项回落到部署声明（再没有就留空 = 内置行为）
      setPersonaId(preferences?.persona_id || personaCatalog?.default_persona_id || "");
      setStyleId(preferences?.style_id || styleCatalog?.default_style_id || "");
      setPresetId(preferences?.preset_id || presetCatalog?.auto_preset_id || "");
      setStPresetId(preferences?.st_preset_id || "");

      const stored = Boolean(
        preferences?.persona_id || preferences?.style_id || preferences?.st_preset_id,
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
      setStatus(`${label}已保存：Web 端与桌宠窗下次打开时按它生效`);
    } catch (error: unknown) {
      setStatus(`保存失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }, []);

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

      <PersonaSwitcher
        value={personaId}
        onChange={(id) => void persist({ persona_id: id }, "人设")}
        catalog={personas}
      />
      <StyleSwitcher
        value={styleId}
        onChange={(id) => void persist({ style_id: id }, "文风")}
        catalog={styles}
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
