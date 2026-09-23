/**
 * 数字人设置面板的**语音引擎 / 音色**区块测试。
 *
 * 重点覆盖三件容易出错的事：
 * 1. 后端**没**服务端 TTS 时不显示音色区（否则会给出一个"选了也没用"的死下拉）；
 * 2. 后端给了音色表时才显示，且「默认音色」是空串（等于后端配置，不是某个 id）；
 * 3. 试听按钮只在后端可试听、且没有正在试听时才可点（防叠一句 / 防误以为没反应）。
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AvatarSettings } from "@/components/avatar/AvatarSettings";
import type { AvatarModelsState } from "@/hooks/avatar/useAvatarModels";
import type { TtsVoicesStatus } from "@/lib/api/types";

const models: AvatarModelsState = {
  models: [],
  loading: false,
  error: null,
  selectedId: "",
  selected: null,
  select: vi.fn(),
  refresh: vi.fn(async () => {}),
  upload: vi.fn(),
  remove: vi.fn(async () => {}),
  updateMapping: vi.fn(async () => {}),
  clearError: vi.fn(),
};

function ttsStatus(overrides: Partial<TtsVoicesStatus> = {}): TtsVoicesStatus {
  return {
    provider: "gpt_sovits",
    server_tts: true,
    configured: true,
    default_voice: "",
    voices: [
      { id: "gentle", label: "温柔", is_default: false },
      { id: "lively", label: "活泼", is_default: false },
    ],
    emotion_voices: false,
    note: "",
    ...overrides,
  };
}

function renderPanel(overrides: Partial<Parameters<typeof AvatarSettings>[0]> = {}) {
  const onTtsEngineChange = vi.fn();
  const onTtsVoiceChange = vi.fn();
  const onPreviewVoice = vi.fn(async () => {});
  render(
    <AvatarSettings
      open
      onClose={vi.fn()}
      credentials={null}
      source="none"
      save={vi.fn(() => true)}
      clear={vi.fn()}
      renderer="auto"
      onRendererChange={vi.fn()}
      ttsEngine="auto"
      onTtsEngineChange={onTtsEngineChange}
      ttsVoice=""
      onTtsVoiceChange={onTtsVoiceChange}
      ttsStatus={ttsStatus()}
      onPreviewVoice={onPreviewVoice}
      models={models}
      {...overrides}
    />,
  );
  return { onTtsEngineChange, onTtsVoiceChange, onPreviewVoice };
}

afterEach(cleanup);

describe("语音引擎区块", () => {
  /** 按 fieldset 限定范围：渲染方式那组也有一个「自动」，全局查会撞名 */
  function engineGroup() {
    return within(screen.getByRole("group", { name: "语音引擎" }));
  }

  it("三档引擎都渲染，且按当前偏好选中", () => {
    renderPanel({ ttsEngine: "browser" });

    const group = engineGroup();
    expect((group.getByRole("radio", { name: /^自动/ }) as HTMLInputElement).checked).toBe(false);
    expect(
      (group.getByRole("radio", { name: /强制浏览器语音/ }) as HTMLInputElement).checked,
    ).toBe(true);
  });

  it("切换引擎会回调（由页面决定生效）", () => {
    const { onTtsEngineChange } = renderPanel();

    fireEvent.click(engineGroup().getByRole("radio", { name: /强制服务端 TTS/ }));

    expect(onTtsEngineChange).toHaveBeenCalledWith("server");
  });

  it("后端未就绪时给出可读说明（前端据此知道会回落浏览器语音）", () => {
    renderPanel({ ttsStatus: null });

    expect(screen.getByText(/未探测到后端语音引擎/)).toBeTruthy();
  });

  it("后端未配好服务端 TTS 时用后端给的原因", () => {
    renderPanel({
      ttsStatus: ttsStatus({ configured: false, voices: [], note: "未配置参考音频（GPT_SOVITS_REF_AUDIO）" }),
    });

    expect(screen.getByText(/未配置参考音频/)).toBeTruthy();
  });
});

describe("音色选择", () => {
  it("服务端 TTS 就绪且有音色表时展示下拉，默认项是空串", () => {
    renderPanel();

    const select = screen.getByRole("combobox", { name: /音色/ }) as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(screen.getByRole("option", { name: "默认音色（后端配置）" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "温柔" })).toBeTruthy();
  });

  it("切换音色会回调", () => {
    const { onTtsVoiceChange } = renderPanel();

    fireEvent.change(screen.getByRole("combobox", { name: /音色/ }), {
      target: { value: "gentle" },
    });

    expect(onTtsVoiceChange).toHaveBeenCalledWith("gentle");
  });

  it("服务端 TTS 未配好时**不**展示音色区（避免死选项）", () => {
    renderPanel({ ttsStatus: ttsStatus({ configured: false }) });

    expect(screen.queryByRole("combobox", { name: /音色/ })).toBeNull();
  });

  it("音色表为空且没有默认音色时不展示下拉", () => {
    renderPanel({ ttsStatus: ttsStatus({ voices: [], default_voice: "" }) });

    expect(screen.queryByRole("combobox", { name: /音色/ })).toBeNull();
  });

  it("提示音色表在后端、改动需重启后端（否则用户会以为改文件立刻生效）", () => {
    renderPanel();

    expect(screen.getByText(/tts_voices\.json/)).toBeTruthy();
    expect(screen.getByText(/需重启后端/)).toBeTruthy();
  });
});

describe("试听", () => {
  it("点击试听把当前音色传给回调", async () => {
    const { onPreviewVoice } = renderPanel({ ttsVoice: "lively" });

    fireEvent.click(screen.getByRole("button", { name: "试听" }));

    await waitFor(() => expect(onPreviewVoice).toHaveBeenCalledWith("lively"));
  });

  it("试听期间按钮变「试听中…」且不可重入（避免叠着说两句）", async () => {
    let resolvePreview!: () => void;
    const onPreviewVoice = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolvePreview = resolve;
        }),
    );
    renderPanel({ onPreviewVoice });

    fireEvent.click(screen.getByRole("button", { name: "试听" }));

    const pending = (await screen.findByRole("button", { name: "试听中…" })) as HTMLButtonElement;
    expect(pending.disabled).toBe(true);
    fireEvent.click(pending);
    expect(onPreviewVoice).toHaveBeenCalledTimes(1);

    resolvePreview();
    await waitFor(() => expect(screen.getByRole("button", { name: "试听" })).toBeTruthy());
  });

  it("试听失败给出可读提示，不把面板搞崩", async () => {
    const onPreviewVoice = vi.fn(async () => {
      throw new Error("boom");
    });
    renderPanel({ onPreviewVoice });

    fireEvent.click(screen.getByRole("button", { name: "试听" }));

    await waitFor(() => expect(screen.getByText(/试听失败/)).toBeTruthy());
  });
});

describe("音色选择：情绪联动文案", () => {
  it("后端配了情绪→音色映射时，「不选音色」的含义是「按情绪自动选」", () => {
    renderPanel({ ttsStatus: ttsStatus({ emotion_voices: true }) });

    expect(screen.getByRole("option", { name: "自动（按情绪选音色）" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "默认音色（后端配置）" })).toBeNull();
    expect(screen.getByText(/按本轮情绪自动选/)).toBeTruthy();
  });

  it("没配映射时保持原文案（不能让用户以为声音会随情绪变）", () => {
    renderPanel({ ttsStatus: ttsStatus({ emotion_voices: false }) });

    expect(screen.getByRole("option", { name: "默认音色（后端配置）" })).toBeTruthy();
    expect(screen.queryByText(/按本轮情绪自动选/)).toBeNull();
  });
});
