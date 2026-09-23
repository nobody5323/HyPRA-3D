import { useEffect, useState } from "react";

import { PersonaSwitcher } from "@/components/settings/PersonaSwitcher";
import { PresetSwitcher } from "@/components/settings/PresetSwitcher";
import { StyleSwitcher } from "@/components/settings/StyleSwitcher";
import { StPresetPanel } from "@/components/studio/StPresetPanel";
import { getPersonas, getPresets, getStyles } from "@/lib/api/client";
import type { PersonaCatalog, PresetCatalog, StyleCatalog } from "@/lib/api/types";

/**
 * 人设与文风分组。
 *
 * 复用 Web 端的四个选择器（它们是同一套组件，所以「有哪些人设/文风」不会两边不一致）。
 *
 * **作用范围要说清楚**：这里的选择是**控制台内的选择**，用来查看与对比各套提示词；
 * 它不会改动正在对话的 Web / 桌宠界面用的那一份——那些在各自界面里选。
 * 之所以不在这里假装能改：人设/文风目前是「会话级状态」，而控制台里没有会话。
 */
export function PersonaPanel() {
  const [personas, setPersonas] = useState<PersonaCatalog | null>(null);
  const [styles, setStyles] = useState<StyleCatalog | null>(null);
  const [presets, setPresets] = useState<PresetCatalog | null>(null);

  const [personaId, setPersonaId] = useState("");
  const [styleId, setStyleId] = useState("");
  const [presetId, setPresetId] = useState("");
  const [stPresetId, setStPresetId] = useState("");

  useEffect(() => {
    let active = true;

    void getPersonas().then((catalog) => {
      if (!active) return;
      setPersonas(catalog);
      // 默认值来自后端声明的缺省，而不是清单首项：配置写错时要看得出来
      setPersonaId(catalog?.default_persona_id ?? "");
    });
    void getStyles().then((catalog) => {
      if (!active) return;
      setStyles(catalog);
      setStyleId(catalog?.default_style_id ?? "");
    });
    void getPresets().then((catalog) => {
      if (!active) return;
      setPresets(catalog);
      setPresetId(catalog?.auto_preset_id ?? "");
    });

    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <p className="rounded-xl bg-surface-inset px-4 py-3 text-xs leading-relaxed text-ink-muted">
        这里用于查看与对比各套提示词（人设 / 文风 / 预设 / 酒馆预设）。
        <strong className="text-ink">对话中实际生效的选择在 Web 端或桌宠窗里</strong>
        ——人设与文风是会话级状态，控制台里没有会话。
      </p>

      <PersonaSwitcher value={personaId} onChange={setPersonaId} catalog={personas} />
      <StyleSwitcher value={styleId} onChange={setStyleId} catalog={styles} />
      <PresetSwitcher value={presetId} onChange={setPresetId} catalog={presets} />
      <StPresetPanel value={stPresetId} onChange={setStPresetId} />
    </div>
  );
}
