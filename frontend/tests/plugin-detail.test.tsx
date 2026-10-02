/**
 * 插件详情与操作区测试（`/plugins/*` 的界面层）。
 *
 * 重点：
 * 1. **必填项拦在提交前**——后端不校验 schema 的 required，前端不拦就会存进半截配置；
 * 2. **启停回传最新快照**——否则页面上的状态徽章不会跟着变；
 * 3. **权限声明必须看得见**——§9.3 说权限是架构约束，那界面就得让人看得见边界；
 * 4. **密钥字段只进不出**——后端永不下发明文，表单不回填、留空沿用、清除要显式；
 * 5. **描述要显示**——「开关在哪」（如语音合成的 DIGITAL_HUMAN_PROVIDER）只在描述里。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PluginDetail } from "@/components/settings/PluginDetail";
import type { PluginStatus } from "@/lib/api/types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}

function stubRoutes(
  handler: (url: string, method: string, body: Record<string, unknown> | null) => Response | undefined,
): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
      calls.push({ url, method, body });
      return handler(url, method, body) ?? jsonResponse({ detail: "not found" }, 404);
    }),
  );
  return calls;
}

function makePlugin(overrides: Partial<PluginStatus> = {}): PluginStatus {
  return {
    id: "tavern-bridge",
    display_name: "酒馆数据接入",
    description: "",
    version: "0.1.0",
    layer: "builtin",
    category: "datasource",
    state: "disabled",
    enabled: false,
    capabilities: ["datasource", "settings"],
    read_only: true,
    error: "",
    ...overrides,
  };
}

const SETTINGS_BODY = {
  id: "tavern-bridge",
  values: {},
  secrets_set: [],
  schema: {
    type: "object",
    properties: {
      tavern_dir: {
        type: "string",
        title: "酒馆数据目录",
        description: "填 default-user 那层",
      },
    },
    required: ["tavern_dir"],
  },
  permissions: {
    filesystem: { read: ["${tavern_dir}"], write: false, write_paths: [] },
    network: { hosts: [] },
  },
};

/** 一张带密钥字段的表（embedding 插件的形状）。 */
const SECRET_SETTINGS_BODY = {
  ...SETTINGS_BODY,
  values: { embedding_model: "text-embedding-v3" },
  secrets_set: ["embedding_api_key"],
  schema: {
    type: "object",
    properties: {
      embedding_api_key: {
        type: "string",
        format: "password",
        title: "API Key",
        description: "云端向量化服务必填",
      },
      embedding_real_key: { type: "string", title: "带默认值的密钥", format: "password", default: "sk-fake" },
    },
  },
};

const isSettingsGet = (url: string, method: string) => url.endsWith("/settings") && method === "GET";

describe("PluginDetail 配置", () => {
  it("展开时读取配置并渲染表单", async () => {
    stubRoutes((url, method) => (isSettingsGet(url, method) ? jsonResponse(SETTINGS_BODY) : undefined));

    render(<PluginDetail plugin={makePlugin()} />);

    expect(await screen.findByLabelText(/酒馆数据目录/)).toBeTruthy();
    expect(screen.getByText("填 default-user 那层")).toBeTruthy();
  });

  it("把权限声明摊给用户看（只读 / 不联网）", async () => {
    stubRoutes((url, method) => (isSettingsGet(url, method) ? jsonResponse(SETTINGS_BODY) : undefined));

    render(<PluginDetail plugin={makePlugin()} />);

    expect(await screen.findByText(/文件系统只读（可读 \$\{tavern_dir\}）/)).toBeTruthy();
    expect(screen.getByText(/网络不访问/)).toBeTruthy();
  });

  it("schema 的 default 会合并进表单（界面显示什么，保存就该写什么）", async () => {
    const body = {
      ...SETTINGS_BODY,
      schema: {
        type: "object",
        properties: { depth: { type: "integer", title: "深度", default: 3 } },
      },
    };
    stubRoutes((url, method) => (isSettingsGet(url, method) ? jsonResponse(body) : undefined));

    render(<PluginDetail plugin={makePlugin()} />);

    const input = (await screen.findByLabelText("深度")) as HTMLInputElement;
    expect(input.value).toBe("3");
  });

  it("必填项缺失时拦住保存，不发 PUT", async () => {
    const calls = stubRoutes((url, method) =>
      isSettingsGet(url, method) ? jsonResponse(SETTINGS_BODY) : undefined,
    );

    render(<PluginDetail plugin={makePlugin()} />);
    await screen.findByLabelText(/酒馆数据目录/);

    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    expect(await screen.findByText(/还有必填项未填：tavern_dir/)).toBeTruthy();
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
  });

  it("保存成功后回传最新快照，并把值发给后端", async () => {
    const onPluginsChanged = vi.fn();
    const calls = stubRoutes((url, method, body) => {
      if (isSettingsGet(url, method)) return jsonResponse(SETTINGS_BODY);
      if (url.endsWith("/settings") && method === "PUT") {
        return jsonResponse({
          id: "tavern-bridge",
          values: body?.values ?? {},
          secrets_set: [],
          plugins: [makePlugin({ enabled: true, state: "started" })],
        });
      }
      return undefined;
    });

    render(<PluginDetail plugin={makePlugin()} onPluginsChanged={onPluginsChanged} />);
    fireEvent.change(await screen.findByLabelText(/酒馆数据目录/), {
      target: { value: "/data/tavern" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    await waitFor(() => expect(onPluginsChanged).toHaveBeenCalled());
    expect(calls.find((call) => call.method === "PUT")?.body).toEqual({
      values: { tavern_dir: "/data/tavern" },
    });
    // 插件未启用时不能宣称「已生效」：覆盖层只收启用中的插件（后端 _collect_runtime_overrides）
    expect(screen.getByText("配置已保存；插件未启用，启用后生效。")).toBeTruthy();
  });

  it("已启用的插件保存后说「并生效」", async () => {
    stubRoutes((url, method, body) => {
      if (isSettingsGet(url, method)) return jsonResponse(SETTINGS_BODY);
      if (url.endsWith("/settings") && method === "PUT") {
        return jsonResponse({
          id: "tavern-bridge",
          values: body?.values ?? {},
          secrets_set: [],
          plugins: [makePlugin({ enabled: true, state: "started" })],
        });
      }
      return undefined;
    });

    render(<PluginDetail plugin={makePlugin({ enabled: true, state: "started" })} />);
    fireEvent.change(await screen.findByLabelText(/酒馆数据目录/), {
      target: { value: "/data/tavern" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    expect(await screen.findByText("配置已保存并生效。")).toBeTruthy();
  });

  it("数值越界时拦住保存（min/max 不会触发原生校验，控件不在 form 里）", async () => {
    const body = {
      ...SETTINGS_BODY,
      schema: {
        type: "object",
        properties: { embedding_dim: { type: "integer", title: "向量维度", minimum: 1 } },
      },
    };
    const calls = stubRoutes((url, method) =>
      isSettingsGet(url, method) ? jsonResponse(body) : undefined,
    );

    render(<PluginDetail plugin={makePlugin()} />);
    fireEvent.change(await screen.findByLabelText("向量维度"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    expect(await screen.findByText(/数值超出允许范围：embedding_dim/)).toBeTruthy();
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
  });

  it("接口失败时给出可读错误", async () => {
    stubRoutes(() => jsonResponse({ detail: "插件不存在：nope" }, 404));

    render(<PluginDetail plugin={makePlugin({ id: "nope" })} />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("插件不存在：nope");
  });
});

describe("PluginDetail 密钥与说明", () => {
  it("显示插件描述（能力是什么、开关在哪都写在这里）", async () => {
    stubRoutes((url, method) => (isSettingsGet(url, method) ? jsonResponse(SETTINGS_BODY) : undefined));

    render(
      <PluginDetail
        plugin={makePlugin({ description: "是否启用服务端语音由 DIGITAL_HUMAN_PROVIDER 决定" })}
      />,
    );

    expect(
      await screen.findByText(/是否启用服务端语音由 DIGITAL_HUMAN_PROVIDER 决定/),
    ).toBeTruthy();
  });

  it("已配置的密钥提示「已保存，留空则沿用」，且不把明文回填", async () => {
    stubRoutes((url, method) =>
      isSettingsGet(url, method) ? jsonResponse(SECRET_SETTINGS_BODY) : undefined,
    );

    render(<PluginDetail plugin={makePlugin()} />);

    const input = (await screen.findByLabelText("API Key")) as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(input.value).toBe("");
    expect(input.placeholder).toContain("留空则沿用");
  });

  it("密钥字段的 schema default 不写进表单（否则会把假 key 存进配置）", async () => {
    const calls = stubRoutes((url, method, body) => {
      if (isSettingsGet(url, method)) return jsonResponse(SECRET_SETTINGS_BODY);
      if (url.endsWith("/settings") && method === "PUT") {
        return jsonResponse({
          id: "tavern-bridge",
          values: body?.values ?? {},
          secrets_set: [],
          plugins: [makePlugin()],
        });
      }
      return undefined;
    });

    render(<PluginDetail plugin={makePlugin()} />);
    await screen.findByLabelText("带默认值的密钥");
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    await waitFor(() => expect(calls.some((call) => call.method === "PUT")).toBe(true));
    const sent = calls.find((call) => call.method === "PUT")?.body as { values: object };
    expect(sent.values).not.toHaveProperty("embedding_real_key");
  });

  it("点「清除已保存」后保存，提交 null（后端据此回落 .env）", async () => {
    const calls = stubRoutes((url, method, body) => {
      if (isSettingsGet(url, method)) return jsonResponse(SECRET_SETTINGS_BODY);
      if (url.endsWith("/settings") && method === "PUT") {
        return jsonResponse({
          id: "tavern-bridge",
          values: body?.values ?? {},
          secrets_set: [],
          plugins: [makePlugin()],
        });
      }
      return undefined;
    });

    render(<PluginDetail plugin={makePlugin()} />);
    fireEvent.click(await screen.findByRole("button", { name: "清除已保存" }));
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    await waitFor(() => expect(calls.some((call) => call.method === "PUT")).toBe(true));
    const sent = calls.find((call) => call.method === "PUT")?.body as {
      values: Record<string, unknown>;
    };
    expect(sent.values.embedding_api_key).toBeNull();
  });
});

describe("PluginDetail 启停", () => {
  it("默认禁用 → 勾选后发 POST 并回传快照", async () => {
    const onPluginsChanged = vi.fn();
    const calls = stubRoutes((url, method) => {
      if (isSettingsGet(url, method)) return jsonResponse(SETTINGS_BODY);
      if (url.endsWith("/enabled") && method === "POST") {
        return jsonResponse({
          id: "tavern-bridge",
          enabled: true,
          plugins: [makePlugin({ enabled: true, state: "started" })],
        });
      }
      return undefined;
    });

    render(<PluginDetail plugin={makePlugin()} onPluginsChanged={onPluginsChanged} />);
    const toggle = (await screen.findByRole("checkbox")) as HTMLInputElement;
    expect(toggle.checked).toBe(false);

    fireEvent.click(toggle);

    await waitFor(() => expect(onPluginsChanged).toHaveBeenCalled());
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({ enabled: true });
    expect(screen.getByText("已启用，本次运行即刻生效。")).toBeTruthy();
  });

  it("core 层不可禁用，并说明原因", async () => {
    stubRoutes((url, method) => (isSettingsGet(url, method) ? jsonResponse(SETTINGS_BODY) : undefined));

    render(
      <PluginDetail
        plugin={makePlugin({ id: "core-demo", layer: "core", enabled: true, state: "started" })}
      />,
    );

    const toggle = (await screen.findByRole("checkbox")) as HTMLInputElement;
    expect(toggle.disabled).toBe(true);
    expect(screen.getByText(/core 层不可禁用：换掉它产品就不成立/)).toBeTruthy();
  });
});
