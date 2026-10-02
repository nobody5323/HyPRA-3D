import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * 桌宠窗的「预设」入口（接线回归用例）。
 *
 * 与 `pet-session-panel.test.ts` 同一做法：断言**源码接线**而不是渲染结果——
 * desktop 的 vitest 跑在 node 环境、没有 DOM 基建（见 vitest.config.mts）。
 *
 * 守住的教训：文风 / 提示词预设 / 酒馆预设原先只能在 Web 端与程序控制台里改，
 * 而桌宠窗是长期开着的那一个窗口。用户在这里聊天时想换个语气，得先开控制台、
 * 改完再回来——现场反馈就是「在桌宠聊天里换不了预设」。这组用例把
 * 「入口存在」「它真的接到会话 hook 上」「不引 Tailwind」三件事钉住。
 */

const PET_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "../src/renderer/pet");

const panel = fs.readFileSync(path.join(PET_DIR, "PresetPanel.tsx"), "utf8");
const settings = fs.readFileSync(path.join(PET_DIR, "SettingsPanel.tsx"), "utf8");
const app = fs.readFileSync(path.join(PET_DIR, "App.tsx"), "utf8");

describe("桌宠窗的预设入口", () => {
  it("四项选择都接了出来（文风 / 提示词预设 / 酒馆预设 / 叙事框架）", () => {
    for (const call of [
      "selectStyle",
      "selectPreset",
      "selectStPreset",
      "selectJailbreak",
    ]) {
      expect(panel).toContain(call);
    }
    expect(panel).toContain("getStyles");
    expect(panel).toContain("getPresets");
    expect(panel).toContain("listStPresets");
    expect(panel).toContain("getJailbreakPresets");
  });

  it("这些能力真的接到 useChatSession 上（而不是空壳 prop）", () => {
    for (const wire of [
      "selectStyle: session.setStyleId",
      "selectPreset: session.setPresetId",
      "selectStPreset: session.setStPresetId",
      "selectJailbreak: session.setJailbreakId",
      "styleId: session.styleId",
      "presetId: session.presetId",
      "stPresetId: session.stPresetId",
      "jailbreakId: session.jailbreakId",
      "busy: session.busy",
    ]) {
      expect(app).toContain(wire);
    }
    expect(settings).toContain("<PresetPanel");
  });

  it("文风必须能显式关掉（酒馆用户的「纯预设体验」）", () => {
    // 文风预设的采样优先级最高，会盖过酒馆预设作者自己的采样意图
    expect(panel).toContain('const STYLE_NONE = "none"');
    expect(panel).toContain("<option value={STYLE_NONE}>");
  });

  it("★ 叙事框架的下拉显示的是解析后的有效值，不是偏好里的原始空串", () => {
    // 空串的含义是「跟随部署默认」，部署默认可能是开启的；直接用空串做 value
    // 会让下拉落到「不使用」上——界面显示关着、实际开着，用户想关也无从下手
    expect(panel).toContain("resolveJailbreakId");
    expect(panel).toContain("value={effectiveJailbreakId}");
    expect(panel).toContain("JAILBREAK_NONE");
  });

  it("对话进行中禁用切换（与角色 / 会话选择器同一口径）", () => {
    expect(panel).toContain("busy");
    expect(panel).toContain("disabled={busy}");
  });

  it("清单为空时不渲染（后端离线时留一排空下拉只会误导）", () => {
    expect(panel).toContain("styleOptions.length === 0");
    expect(panel).toContain("return null");
  });

  it("用 pet.css 的类，不引 Tailwind 工具类（桌宠入口没加载 Tailwind）", () => {
    for (const forbidden of [
      'className="rounded',
      "text-xs",
      "border-line",
      "bg-surface",
      "focus-ring",
    ]) {
      expect(panel).not.toContain(forbidden);
    }
    expect(panel).toContain("petField");
    expect(panel).toContain("petHint");
  });

  it("不写死任何预设名（一律来自后端清单）", () => {
    expect(panel).not.toContain("modern-conversational");
    expect(panel).not.toContain("简短利落");
  });
});
