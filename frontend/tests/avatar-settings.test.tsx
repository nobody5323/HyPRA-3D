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
  updateLayout: vi.fn(),
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
      save={vi.fn(async () => true)}
      clear={vi.fn(async () => true)}
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

  it("两档引擎都渲染，且按当前偏好选中（已无「浏览器语音」档）", () => {
    renderPanel({ ttsEngine: "server" });

    const group = engineGroup();
    expect((group.getByRole("radio", { name: /^自动/ }) as HTMLInputElement).checked).toBe(false);
    expect(
      (group.getByRole("radio", { name: /强制服务端 TTS/ }) as HTMLInputElement).checked,
    ).toBe(true);
    expect(group.queryByRole("radio", { name: /浏览器/ })).toBeNull();
  });

  it("切换引擎会回调（由页面决定生效）", () => {
    const { onTtsEngineChange } = renderPanel();

    fireEvent.click(engineGroup().getByRole("radio", { name: /强制服务端 TTS/ }));

    expect(onTtsEngineChange).toHaveBeenCalledWith("server");
  });

  it("后端未就绪时给出可读说明（前端据此知道本轮不会播报）", () => {
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

/**
 * 凭证保存 / 清除。
 *
 * 凭证现在存在**后端**（三个界面共享同一份），所以这条路径有两个新性质：
 * 写失败必须让用户看得见（不再有「写入本机 localStorage 失败」这种静默情形），
 * 提示文案也不能再声称「只保存在本机浏览器」。
 */
describe("凭证保存与清除（走后端）", () => {
  function fillCredentials(appId: string, appSecret: string) {
    fireEvent.change(screen.getByRole("textbox", { name: /App ID/ }), {
      target: { value: appId },
    });
    fireEvent.change(screen.getByPlaceholderText("••••••••"), { target: { value: appSecret } });
  }

  it("保存把两个字段交给回调，并提示会自动重新初始化", async () => {
    const save = vi.fn(async () => true);
    renderPanel({ save });

    fillCredentials("ak-1", "sk-1");
    fireEvent.click(screen.getByRole("button", { name: "保存并启用" }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({ appId: "ak-1", appSecret: "sk-1" }),
    );
    expect(await screen.findByText(/已保存/)).toBeTruthy();
  });

  it("后端不可达 / 被拒时给出可读提示（不静默失败）", async () => {
    renderPanel({ save: vi.fn(async () => false) });

    fillCredentials("ak-1", "sk-1");
    fireEvent.click(screen.getByRole("button", { name: "保存并启用" }));

    expect(await screen.findByText(/保存失败/)).toBeTruthy();
  });

  it("清除成功时清空输入并提示", async () => {
    const clear = vi.fn(async () => true);
    renderPanel({ credentials: { appId: "ak-1", appSecret: "sk-1" }, clear });

    const appIdInput = screen.getByRole("textbox", { name: /App ID/ }) as HTMLInputElement;
    expect(appIdInput.value).toBe("ak-1");

    fireEvent.click(screen.getByRole("button", { name: "清除" }));

    await waitFor(() => expect(clear).toHaveBeenCalled());
    // 等一拍：handleClear 是异步的，输入框清空在 await 之后
    await waitFor(() => expect(appIdInput.value).toBe(""));
    expect(await screen.findByText(/已清除后端保存的密钥/)).toBeTruthy();
  });

  it("清除失败时不假装成功（输入保持原值）", async () => {
    renderPanel({
      credentials: { appId: "ak-1", appSecret: "sk-1" },
      clear: vi.fn(async () => false),
    });

    fireEvent.click(screen.getByRole("button", { name: "清除" }));

    expect(await screen.findByText(/清除失败/)).toBeTruthy();
    expect((screen.getByRole("textbox", { name: /App ID/ }) as HTMLInputElement).value).toBe(
      "ak-1",
    );
  });

  it("底部说明如实写明密钥存后端，不再声称只在本机", () => {
    renderPanel();

    expect(screen.getByText(/backend\/data\/avatar-credentials\.json/)).toBeTruthy();
    expect(screen.queryByText(/仅保存在本机浏览器/)).toBeNull();
  });
});
