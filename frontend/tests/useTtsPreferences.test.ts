/**
 * 语音引擎与音色偏好（`useTtsPreferences`）测试。
 *
 * 锁三件事：
 * 1. 探测失败/后端未就绪 → `status=null`、`serverTtsAvailable=false`（上层据此回落浏览器语音），
 *    **绝不抛错**；
 * 2. 偏好能从 localStorage 读回（挂载后同步，首屏不读）；
 * 3. 音色失效回落：音色表换了之后，本地记的 id 不存在了必须自动清掉，
 *    否则界面会显示一个「选了却没有任何变化」的死选项。
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTtsPreferences } from "@/hooks/avatar/useTtsPreferences";
import type { TtsVoicesStatus } from "@/lib/api/types";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function ttsStatus(overrides: Partial<TtsVoicesStatus> = {}): TtsVoicesStatus {
  return {
    provider: "gpt_sovits",
    server_tts: true,
    configured: true,
    default_voice: "",
    voices: [{ id: "gentle", label: "温柔", is_default: false }],
    emotion_voices: false,
    note: "",
    ...overrides,
  };
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useTtsPreferences：探测", () => {
  it("后端就绪时才探测，探测结果决定 serverTtsAvailable", async () => {
    const fetchMock = vi.fn(async (_url: string) => jsonResponse(ttsStatus()));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTtsPreferences({ enabled: true }));

    await waitFor(() => expect(result.current.status).not.toBeNull());
    expect(result.current.serverTtsAvailable).toBe(true);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/media/tts/voices");
  });

  it("后端未就绪（enabled=false）时完全不请求", () => {
    const fetchMock = vi.fn(async (_url: string) => jsonResponse(ttsStatus()));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTtsPreferences({ enabled: false }));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.serverTtsAvailable).toBe(false);
  });

  it("探测失败时 status 为 null 而不抛错（上层回落浏览器语音）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("offline");
      }),
    );

    const { result } = renderHook(() => useTtsPreferences({ enabled: true }));

    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.status).toBeNull();
    expect(result.current.serverTtsAvailable).toBe(false);
  });

  it("驱动会出音频但未配好时，serverTtsAvailable 仍为 false", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string) => jsonResponse(ttsStatus({ configured: false }))));

    const { result } = renderHook(() => useTtsPreferences({ enabled: true }));

    await waitFor(() => expect(result.current.status).not.toBeNull());
    expect(result.current.serverTtsAvailable).toBe(false);
  });
});

describe("useTtsPreferences：偏好", () => {
  it("首屏用安全默认值，挂载后从 localStorage 读回", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string) => jsonResponse(ttsStatus())));
    window.localStorage.setItem("hypra.avatar.tts-engine", "browser");
    window.localStorage.setItem("hypra.avatar.tts-voice", "gentle");

    const { result } = renderHook(() => useTtsPreferences({ enabled: false }));

    await waitFor(() => expect(result.current.engine).toBe("browser"));
    expect(result.current.voice).toBe("gentle");
  });

  it("设置后同步写回 localStorage（跨刷新保持）", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string) => jsonResponse(ttsStatus())));
    const { result } = renderHook(() => useTtsPreferences({ enabled: false }));

    act(() => {
      result.current.setEngine("server");
      result.current.setVoice("gentle");
    });

    expect(window.localStorage.getItem("hypra.avatar.tts-engine")).toBe("server");
    expect(window.localStorage.getItem("hypra.avatar.tts-voice")).toBe("gentle");
    expect(result.current.engine).toBe("server");
    expect(result.current.voice).toBe("gentle");
  });

  it("非法偏好值不写入（避免脏值进了存储）", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string) => jsonResponse(ttsStatus())));
    const { result } = renderHook(() => useTtsPreferences({ enabled: false }));

    act(() => {
      result.current.setEngine("nope" as never);
    });

    expect(window.localStorage.getItem("hypra.avatar.tts-engine")).toBeNull();
  });

  it("清空音色会删除存储项（回落后端默认音色）", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string) => jsonResponse(ttsStatus())));
    const { result } = renderHook(() => useTtsPreferences({ enabled: false }));

    act(() => {
      result.current.setVoice("gentle");
    });
    act(() => {
      result.current.setVoice("");
    });

    expect(window.localStorage.getItem("hypra.avatar.tts-voice")).toBeNull();
    expect(result.current.voice).toBe("");
  });
});

describe("useTtsPreferences：音色失效回落", () => {
  it("保存的音色已不在音色表里时自动清空（否则是「选了没反应」的死选项）", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string) => jsonResponse(ttsStatus())));
    window.localStorage.setItem("hypra.avatar.tts-voice", "已删除的音色");

    const { result } = renderHook(() => useTtsPreferences({ enabled: true }));

    await waitFor(() => expect(result.current.voice).toBe(""));
    expect(window.localStorage.getItem("hypra.avatar.tts-voice")).toBeNull();
  });

  it("音色仍在表里时保持不变", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string) => jsonResponse(ttsStatus())));
    window.localStorage.setItem("hypra.avatar.tts-voice", "gentle");

    const { result } = renderHook(() => useTtsPreferences({ enabled: true }));

    await waitFor(() => expect(result.current.status).not.toBeNull());
    expect(result.current.voice).toBe("gentle");
  });
});
