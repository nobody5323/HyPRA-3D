/**
 * 数字人凭证（`useAvatarCredentials`）测试。
 *
 * 锁四件事：
 * 1. 凭证来自**后端**，首屏不读 localStorage（SSR/CSR 才一致）；
 * 2. 后端说「没配」时，把**旧版** localStorage 里的凭证搬过去（一次性迁移），
 *    搬完清掉旧键；后端已经有值时不迁移（否则会覆盖用户后来改的）；
 * 3. 后端读不到（离线 / 5xx）时保持现状，不把用户看着的渲染器换掉；
 * 4. 保存 / 清除走写接口，并推进 revision（否则上层不会重建魔珐会话）。
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAvatarCredentials } from "@/hooks/avatar/useAvatarCredentials";
import type { AvatarCredentialsResponse } from "@/lib/api/types";

/** 旧版存凭证用的键（与 avatar-config.ts 的迁移入口保持一致） */
const LEGACY_KEY = "hypra.avatar.credentials";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function credentials(
  overrides: Partial<AvatarCredentialsResponse> = {},
): AvatarCredentialsResponse {
  return {
    form: "web",
    appId: "ak-1",
    appSecret: "sk-1",
    source: "user",
    configured: true,
    ...overrides,
  };
}

/** 未配置的后端响应 */
const UNCONFIGURED_RESPONSE: AvatarCredentialsResponse = credentials({
  appId: "",
  appSecret: "",
  source: "none",
  configured: false,
});

/** 替换全局 fetch，并记下每次请求（便于断言打到了哪个接口、带了什么 body） */
function stubFetch(handler: (url: string, init?: RequestInit) => Promise<Response>) {
  const calls: Array<{ url: string; method: string; body: unknown }> = [];
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({
      url: String(url),
      method: (init?.method ?? "GET").toUpperCase(),
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return handler(String(url), init);
  });
  vi.stubGlobal("fetch", mock);
  return calls;
}

function writeLegacy(value: unknown): void {
  window.localStorage.setItem(LEGACY_KEY, JSON.stringify(value));
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useAvatarCredentials：读后端", () => {
  it("挂载后按形态拉凭证", async () => {
    const calls = stubFetch(async () => jsonResponse(credentials({ form: "pet" })));

    const { result } = renderHook(() => useAvatarCredentials({ form: "pet" }));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(calls[0].url).toContain("/media/avatar/credentials?form=pet");
    expect(result.current.credentials).toEqual({ appId: "ak-1", appSecret: "sk-1" });
    expect(result.current.source).toBe("user");
    expect(result.current.configured).toBe(true);
  });

  it("首屏是「未配置」：不从 localStorage 取初始值（否则 SSR/CSR 不一致）", () => {
    writeLegacy({ v: 1, appId: "ak-old", appSecret: "sk-old" });
    // 请求挂着不返回：观察到的就是首屏状态
    stubFetch(() => new Promise<Response>(() => {}));

    const { result } = renderHook(() => useAvatarCredentials());

    expect(result.current.credentials).toBeNull();
    expect(result.current.configured).toBe(false);
    expect(result.current.source).toBe("none");
  });

  it("后端说没配时保持未配置（界面回落到本地渲染器）", async () => {
    stubFetch(async () => jsonResponse(UNCONFIGURED_RESPONSE));

    const { result } = renderHook(() => useAvatarCredentials());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.source).toBe("none");
    expect(result.current.credentials).toBeNull();
  });

  it("后端读不到（离线）时不抛错，按未配置处理", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("offline");
      }),
    );

    const { result } = renderHook(() => useAvatarCredentials());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.credentials).toBeNull();
  });

  it("部署配置兜底时如实上报来源（env，而不是 user）", async () => {
    stubFetch(async () => jsonResponse(credentials({ source: "env" })));

    const { result } = renderHook(() => useAvatarCredentials());

    await waitFor(() => expect(result.current.source).toBe("env"));
  });
});

describe("useAvatarCredentials：旧版 localStorage 一次性迁移", () => {
  it("后端没配 + 本机有旧凭证 → 搬到后端并清掉旧键", async () => {
    writeLegacy({ v: 1, appId: "ak-old", appSecret: "sk-old" });
    const calls = stubFetch(async (_url, init) =>
      init?.method === "PUT"
        ? jsonResponse(credentials({ appId: "ak-old", appSecret: "sk-old" }))
        : jsonResponse(UNCONFIGURED_RESPONSE),
    );

    const { result } = renderHook(() => useAvatarCredentials());

    await waitFor(() => expect(result.current.credentials).not.toBeNull());
    expect(calls.map((call) => call.method)).toEqual(["GET", "PUT"]);
    expect(calls[1].body).toEqual({ form: "web", appId: "ak-old", appSecret: "sk-old" });
    expect(result.current.credentials).toEqual({ appId: "ak-old", appSecret: "sk-old" });
    // 清掉旧键：否则每次挂载都会把同一份旧数据再搬一次
    expect(window.localStorage.getItem(LEGACY_KEY)).toBeNull();
    // revision 必须推进，否则上层不会重建魔珐会话
    expect(result.current.revision).toBe(1);
  });

  it("后端已有配置时不迁移（否则会覆盖用户后来改的）", async () => {
    writeLegacy({ v: 1, appId: "ak-old", appSecret: "sk-old" });
    const calls = stubFetch(async () => jsonResponse(credentials()));

    const { result } = renderHook(() => useAvatarCredentials());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(calls).toHaveLength(1);
    expect(result.current.credentials).toEqual({ appId: "ak-1", appSecret: "sk-1" });
  });

  it("旧数据版本不认识时忽略（宁可让用户重填，也不读脏值）", async () => {
    writeLegacy({ v: 99, appId: "ak-old", appSecret: "sk-old" });
    const calls = stubFetch(async () => jsonResponse(UNCONFIGURED_RESPONSE));

    const { result } = renderHook(() => useAvatarCredentials());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(calls).toHaveLength(1);
    expect(result.current.credentials).toBeNull();
  });

  it("迁移失败（后端拒绝）时保留旧键，下次挂载还能再试", async () => {
    writeLegacy({ v: 1, appId: "ak-old", appSecret: "sk-old" });
    stubFetch(async (_url, init) =>
      init?.method === "PUT"
        ? jsonResponse({ detail: "nope" }, 400)
        : jsonResponse(UNCONFIGURED_RESPONSE),
    );

    const { result } = renderHook(() => useAvatarCredentials());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.credentials).toBeNull();
    expect(window.localStorage.getItem(LEGACY_KEY)).not.toBeNull();
  });
});

describe("useAvatarCredentials：保存与清除", () => {
  it("保存走 PUT，去掉前后空白并更新快照 + revision", async () => {
    const calls = stubFetch(async (_url, init) =>
      init?.method === "PUT" ? jsonResponse(credentials()) : jsonResponse(UNCONFIGURED_RESPONSE),
    );

    const { result } = renderHook(() => useAvatarCredentials());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let ok = false;
    await act(async () => {
      ok = await result.current.save({ appId: " ak-1 ", appSecret: " sk-1 " });
    });

    expect(ok).toBe(true);
    expect(calls[1].method).toBe("PUT");
    // 前后空白必须去掉：肉眼一样的密钥带空格会让 SDK 鉴权失败
    expect(calls[1].body).toEqual({ form: "web", appId: "ak-1", appSecret: "sk-1" });
    expect(result.current.credentials).toEqual({ appId: "ak-1", appSecret: "sk-1" });
    expect(result.current.revision).toBe(1);
  });

  it("后端拒绝时返回 false，且不推进 revision、不假装已保存", async () => {
    stubFetch(async (_url, init) =>
      init?.method === "PUT"
        ? jsonResponse({ detail: "nope" }, 400)
        : jsonResponse(UNCONFIGURED_RESPONSE),
    );

    const { result } = renderHook(() => useAvatarCredentials());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let ok = true;
    await act(async () => {
      ok = await result.current.save({ appId: "ak-1", appSecret: "sk-1" });
    });

    expect(ok).toBe(false);
    expect(result.current.revision).toBe(0);
    expect(result.current.credentials).toBeNull();
  });

  it("清除传空串（后端据此删键）并推进 revision", async () => {
    const calls = stubFetch(async (_url, init) =>
      init?.method === "PUT" ? jsonResponse(UNCONFIGURED_RESPONSE) : jsonResponse(credentials()),
    );

    const { result } = renderHook(() => useAvatarCredentials());
    await waitFor(() => expect(result.current.credentials).not.toBeNull());

    let ok = false;
    await act(async () => {
      ok = await result.current.clear();
    });

    expect(ok).toBe(true);
    expect(calls[1].body).toEqual({ form: "web", appId: "", appSecret: "" });
    expect(result.current.credentials).toBeNull();
    expect(result.current.source).toBe("none");
    expect(result.current.revision).toBe(1);
  });

  it("保存到哪一形态由 form 决定（桌宠窗传 pet 不会写坏 Web 端的）", async () => {
    const calls = stubFetch(async (_url, init) =>
      init?.method === "PUT"
        ? jsonResponse(credentials({ form: "pet" }))
        : jsonResponse(UNCONFIGURED_RESPONSE),
    );

    const { result } = renderHook(() => useAvatarCredentials({ form: "pet" }));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.save({ appId: "ak-pet", appSecret: "sk-pet" });
    });

    expect(calls[0].url).toContain("form=pet");
    expect(calls[1].body).toEqual({ form: "pet", appId: "ak-pet", appSecret: "sk-pet" });
  });
});
