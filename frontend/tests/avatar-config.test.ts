/**
 * 渲染方式偏好测试。
 *
 * 这个开关解决的问题是：「三层渲染降级链默认优先魔珐」——
 * 只看密钥有无时，填了密钥就永远走魔珐，本地渲染器（Live2D）根本没机会跑。
 * 因此这里锁三件事：能存能读、非法值不崩、UI 选项与合法值集合不漂移。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  AVATAR_RENDERER_OPTIONS,
  AVATAR_RENDERER_PREFERENCES,
  DEFAULT_RENDERER_PREFERENCE,
  getInitialRendererPreference,
  getInitialSelectedModelId,
  readRendererPreference,
  readSelectedModelId,
  saveRendererPreference,
  saveSelectedModelId,
  DEFAULT_TTS_ENGINE,
  TTS_ENGINE_OPTIONS,
  TTS_ENGINE_PREFERENCES,
  getInitialTtsEnginePreference,
  getInitialTtsVoice,
  readTtsEnginePreference,
  readTtsVoice,
  resolveVoiceSource,
  saveTtsEnginePreference,
  saveTtsVoice,
  subscribeRendererPreference,
  subscribeSelectedModel,
  subscribeTtsEnginePreference,
  subscribeTtsVoice,
} from "@/lib/avatar-config";

const STORAGE_KEY = "hypra.avatar.renderer";

beforeEach(() => {
  window.localStorage.clear();
});

describe("渲染方式偏好", () => {
  it("未设置时回落到 auto", () => {
    expect(readRendererPreference()).toBe(DEFAULT_RENDERER_PREFERENCE);
    expect(DEFAULT_RENDERER_PREFERENCE).toBe("auto");
  });

  it("保存后能读回，四种取值都支持", () => {
    for (const value of AVATAR_RENDERER_PREFERENCES) {
      expect(saveRendererPreference(value)).toBe(true);
      expect(readRendererPreference()).toBe(value);
    }
  });

  it("localStorage 里是脏值时回落默认值，而不是把脏值当合法值用", () => {
    window.localStorage.setItem(STORAGE_KEY, "live2d-v2");
    expect(readRendererPreference()).toBe(DEFAULT_RENDERER_PREFERENCE);

    window.localStorage.setItem(STORAGE_KEY, "");
    expect(readRendererPreference()).toBe(DEFAULT_RENDERER_PREFERENCE);
  });

  it("非法入参不落盘（并返回 false）", () => {
    // @ts-expect-error 故意传非法值：必须被拦下
    expect(saveRendererPreference("xmov-force")).toBe(false);
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("首屏安全值不读 localStorage（SSR/CSR 必须一致，否则 hydration 报错）", () => {
    saveRendererPreference("static");
    // 即使已经存了 static，首屏仍按默认值渲染，挂载后再切
    expect(getInitialRendererPreference()).toBe(DEFAULT_RENDERER_PREFERENCE);
  });

  it("保存时通知订阅者（页面据此即时切换渲染器）", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeRendererPreference(listener);

    saveRendererPreference("local");
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    saveRendererPreference("static");
    expect(listener).toHaveBeenCalledTimes(1); // 退订后不再收到
  });

  it("存储不可用（隐私模式 / 配额满）时返回 false 而不是抛错", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });

    expect(() => saveRendererPreference("local")).not.toThrow();
    expect(saveRendererPreference("local")).toBe(false);
  });
});

describe("UI 选项与合法取值", () => {
  it("选项清单与合法值集合完全一致（不会漏项或多出无效项）", () => {
    expect(AVATAR_RENDERER_OPTIONS.map((option) => option.value).sort()).toEqual(
      [...AVATAR_RENDERER_PREFERENCES].sort(),
    );
  });

  it("每个选项都有标签与说明", () => {
    for (const option of AVATAR_RENDERER_OPTIONS) {
      expect(option.label.length).toBeGreaterThan(0);
      expect(option.hint.length).toBeGreaterThan(0);
    }
  });
});

describe("选中的模型", () => {
  it("默认是空串 = 用项目内置模型", () => {
    expect(readSelectedModelId()).toBe("");
  });

  it("保存后可读回", () => {
    expect(saveSelectedModelId("m_abc123")).toBe(true);
    expect(readSelectedModelId()).toBe("m_abc123");
  });

  it("保存空串 = 清除选择（回到内置模型，而不是残留一个空 key）", () => {
    saveSelectedModelId("m_abc123");
    saveSelectedModelId("");

    expect(readSelectedModelId()).toBe("");
    expect(window.localStorage.getItem("hypra.avatar.model")).toBeNull();
  });

  it("首屏安全值不读 localStorage（SSR/CSR 必须一致）", () => {
    saveSelectedModelId("m_xyz");
    expect(getInitialSelectedModelId()).toBe("");
  });

  it("保存时通知订阅者", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeSelectedModel(listener);

    saveSelectedModelId("m_1");
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    saveSelectedModelId("m_2");
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

/**
 * 播报实现选择。
 *
 * 这里锁的是**设计约束**（不是偏好）：魔珐 SDK 的 speak() 只吃 SSML、
 * 不接受外部音频，所以走魔珐时必须用它的自带 TTS；
 * 其余情况服务端 TTS（GPT-SoVITS）优先，最后才是浏览器 TTS。
 */
describe("resolveVoiceSource：播报实现选择", () => {
  it("魔珐路径永远用自带 TTS（SDK 不接受外部音频）", () => {
    expect(resolveVoiceSource({ xmovActive: true, serverTtsAvailable: true })).toBe("xmov");
    expect(resolveVoiceSource({ xmovActive: true, serverTtsAvailable: false })).toBe("xmov");
  });

  it("非魔珐时服务端 TTS 优先于浏览器 TTS", () => {
    expect(resolveVoiceSource({ xmovActive: false, serverTtsAvailable: true })).toBe("server");
  });

  it("后端起不来/未配置时回落浏览器 TTS（零依赖，必须能出声）", () => {
    expect(resolveVoiceSource({ xmovActive: false, serverTtsAvailable: false })).toBe("browser");
  });
});

describe("resolveVoiceSource：用户偏好覆盖", () => {
  it("强制浏览器：即使后端服务端 TTS 可用也不用（省算力 / 降延迟）", () => {
    expect(
      resolveVoiceSource({ xmovActive: false, serverTtsAvailable: true, preference: "browser" }),
    ).toBe("browser");
  });

  it("强制服务端：探测失败也先试一次（真失败会逐句降级，不会没声音）", () => {
    expect(
      resolveVoiceSource({ xmovActive: false, serverTtsAvailable: false, preference: "server" }),
    ).toBe("server");
  });

  it("魔珐渲染时偏好被忽略（SDK 只接受 SSML，接不了外部音频）", () => {
    for (const preference of TTS_ENGINE_PREFERENCES) {
      expect(resolveVoiceSource({ xmovActive: true, serverTtsAvailable: true, preference })).toBe(
        "xmov",
      );
    }
  });

  it("不传偏好时等同于 auto", () => {
    expect(resolveVoiceSource({ xmovActive: false, serverTtsAvailable: true })).toBe("server");
    expect(resolveVoiceSource({ xmovActive: false, serverTtsAvailable: false })).toBe("browser");
  });
});

describe("语音引擎偏好", () => {
  it("未设置时回落到 auto，且 UI 选项与合法值集合不漂移", () => {
    expect(readTtsEnginePreference()).toBe(DEFAULT_TTS_ENGINE);
    expect(DEFAULT_TTS_ENGINE).toBe("auto");
    expect(TTS_ENGINE_OPTIONS.map((option) => option.value)).toEqual([...TTS_ENGINE_PREFERENCES]);
  });

  it("保存后能读回，三档都支持", () => {
    for (const value of TTS_ENGINE_PREFERENCES) {
      expect(saveTtsEnginePreference(value)).toBe(true);
      expect(readTtsEnginePreference()).toBe(value);
    }
  });

  it("首屏安全值不读 localStorage（避免 hydration 不一致）", () => {
    window.localStorage.setItem("hypra.avatar.tts-engine", "browser");
    expect(getInitialTtsEnginePreference()).toBe("auto");
  });

  it("非法值被拒绝且不影响已存值", () => {
    expect(saveTtsEnginePreference("nope" as never)).toBe(false);
    window.localStorage.setItem("hypra.avatar.tts-engine", "nope");
    expect(readTtsEnginePreference()).toBe("auto");
  });

  it("变更会通知订阅者，取消订阅后不再通知", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeTtsEnginePreference(listener);

    saveTtsEnginePreference("server");
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    saveTtsEnginePreference("browser");
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe("音色偏好", () => {
  it("未设置时是空串（= 后端默认音色）", () => {
    expect(readTtsVoice()).toBe("");
    expect(getInitialTtsVoice()).toBe("");
  });

  it("保存 / 清空 / 通知", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeTtsVoice(listener);

    expect(saveTtsVoice("gentle")).toBe(true);
    expect(readTtsVoice()).toBe("gentle");

    saveTtsVoice("");                 // 空串 = 回落后端默认音色，存储项必须一起删
    expect(readTtsVoice()).toBe("");
    expect(window.localStorage.getItem("hypra.avatar.tts-voice")).toBeNull();
    expect(listener).toHaveBeenCalledTimes(2);

    unsubscribe();
  });

  it("首屏安全值是空串（不读 localStorage）", () => {
    window.localStorage.setItem("hypra.avatar.tts-voice", "gentle");
    expect(getInitialTtsVoice()).toBe("");
  });
});
