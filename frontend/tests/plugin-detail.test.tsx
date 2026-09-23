/**
 * 插件详情与操作区测试（`/plugins/*` 的界面层）。
 *
 * 重点：
 * 1. **必填项拦在提交前**——后端不校验 schema 的 required，前端不拦就会存进半截配置；
 * 2. **启停回传最新快照**——否则页面上的状态徽章不会跟着变；
 * 3. **权限声明必须看得见**——§9.3 说权限是架构约束，那界面就得让人看得见边界。
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
    expect(screen.getByText("配置已保存并生效。")).toBeTruthy();
  });

  it("接口失败时给出可读错误", async () => {
    stubRoutes(() => jsonResponse({ detail: "插件不存在：nope" }, 404));

    render(<PluginDetail plugin={makePlugin({ id: "nope" })} />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("插件不存在：nope");
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
