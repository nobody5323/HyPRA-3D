/**
 * 能力中心面板测试：分层渲染、状态展示、只读标记、失败原因、空数据不渲染，
 * 以及展开详情 / 手风琴 / 记忆导入区块的挂载条件。
 *
 * 列表数据由页面从 `/health` 取回后传入；**详情是按需拉的**，
 * 因此只有展开那一行时才会看到 `/plugins/{id}/settings` 请求。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PluginCenter } from "@/components/settings/PluginCenter";
import type { PluginStatus, PluginsSummary } from "@/lib/api/types";

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

function stubRoutes(
  handler: (url: string, method: string, body: Record<string, unknown> | null) => Response | undefined,
): string[] {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
      urls.push(`${method} ${url}`);
      return handler(url, method, body) ?? jsonResponse({ detail: "not found" }, 404);
    }),
  );
  return urls;
}

function makePlugin(overrides: Partial<PluginStatus> = {}): PluginStatus {
  return {
    id: "llm-providers",
    display_name: "对话模型接入",
    description: "LLM provider 工厂",
    version: "0.1.0",
    layer: "builtin",
    category: "provider",
    state: "started",
    enabled: true,
    capabilities: ["provider"],
    read_only: true,
    error: "",
    ...overrides,
  };
}

function makeSummary(overrides: Partial<PluginsSummary> = {}): PluginsSummary {
  return { total: 1, started: 1, failed: 0, by_layer: {}, ...overrides };
}

describe("PluginCenter", () => {
  it("后端未就绪（无插件数据）时不渲染", () => {
    const { container } = render(<PluginCenter plugins={[]} summary={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("显示标题与运行统计", () => {
    render(
      <PluginCenter
        plugins={[makePlugin()]}
        summary={makeSummary({ total: 7, started: 6, failed: 1 })}
      />,
    );

    expect(screen.getByText("能力中心")).toBeTruthy();
    expect(screen.getByText(/6\/7 运行中/)).toBeTruthy();
    expect(screen.getByText(/1 失败/)).toBeTruthy();
  });

  it("按 core / builtin / third-party 分层分组，且只渲染存在的层", () => {
    render(
      <PluginCenter
        plugins={[
          makePlugin({ id: "c1", display_name: "核心甲", layer: "core", read_only: false }),
          makePlugin({ id: "b1", display_name: "内置乙" }),
          makePlugin({ id: "b2", display_name: "内置丙" }),
        ]}
        summary={makeSummary()}
      />,
    );

    expect(screen.getByText("核心（1）")).toBeTruthy();
    expect(screen.getByText("内置（2）")).toBeTruthy();
    expect(screen.queryByText(/第三方/)).toBeNull(); // 无第三方插件 → 整层不渲染
    expect(screen.getByText("核心甲")).toBeTruthy();
    expect(screen.getByText("内置丙")).toBeTruthy();
  });

  it("运行中/已禁用/失败 三种状态各有对应文案", () => {
    render(
      <PluginCenter
        plugins={[
          makePlugin({ id: "a", display_name: "甲", state: "started" }),
          makePlugin({ id: "b", display_name: "乙", state: "disabled" }),
          makePlugin({ id: "c", display_name: "丙", state: "failed" }),
        ]}
        summary={makeSummary()}
      />,
    );

    expect(screen.getByText("运行中")).toBeTruthy();
    expect(screen.getByText("已禁用")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
  });

  it("只读插件打上「只读」标记，可写插件不打", () => {
    render(
      <PluginCenter
        plugins={[
          makePlugin({ id: "ro", display_name: "只读插件", read_only: true }),
          makePlugin({ id: "rw", display_name: "可写插件", read_only: false }),
        ]}
        summary={makeSummary()}
      />,
    );

    // 只读标记只出现一次（可写插件不渲染该标记）
    expect(screen.getAllByText("只读")).toHaveLength(1);
    expect(screen.getByTitle(/宿主未授予写句柄/)).toBeTruthy();
  });

  it("失败插件显示错误原因", () => {
    render(
      <PluginCenter
        plugins={[
          makePlugin({
            id: "bad",
            display_name: "坏插件",
            state: "failed",
            error: "入口文件不存在：plugin.py",
          }),
        ]}
        summary={makeSummary({ failed: 1 })}
      />,
    );

    expect(screen.getByText("入口文件不存在：plugin.py")).toBeTruthy();
  });

  it("展示插件贡献的能力面", () => {
    render(
      <PluginCenter
        plugins={[makePlugin({ capabilities: ["datasource", "tool", "settings"] })]}
        summary={makeSummary()}
      />,
    );

    expect(screen.getByText("datasource · tool · settings")).toBeTruthy();
  });

  it("无 summary 时只渲染列表，不报错", () => {
    render(<PluginCenter plugins={[makePlugin()]} summary={null} />);
    expect(screen.getByText("对话模型接入")).toBeTruthy();
    expect(screen.queryByText(/运行中\b.*\//)).toBeNull();
  });
});

describe("PluginCenter 添加插件（§9.3：插件由用户自己写）", () => {
  const DIRS = {
    user_dir: "backend/data/plugins",
    extra_dirs: ["D:/my-plugins"],
    builtin_dir: "backend/plugins",
  };

  const LIST_BODY = {
    plugins: [makePlugin()],
    summary: makeSummary(),
    capabilities: {},
    dirs: DIRS,
  };

  it("展开面板：说清没有沙箱、插件放哪儿、接口文档在哪", async () => {
    stubRoutes((url, method) =>
      url.endsWith("/plugins") && method === "GET" ? jsonResponse(LIST_BODY) : undefined,
    );

    render(<PluginCenter plugins={[makePlugin()]} summary={null} />);
    fireEvent.click(screen.getByRole("button", { name: "添加插件" }));

    // 安全口径不许淡化
    expect(await screen.findByText(/没有沙箱/)).toBeTruthy();
    // 接口与示例的位置（用户据此自己写插件）
    expect(screen.getByText(/docs\/plugin-development\.md/)).toBeTruthy();
    expect(screen.getByText(/docs\/examples\/plugin-hello\//)).toBeTruthy();
    // 扫描目录按需拉取：用户目录 + 额外来源目录都要可见
    expect(await screen.findByText("backend/data/plugins")).toBeTruthy();
    expect(screen.getByText(/D:\/my-plugins/)).toBeTruthy();
  });

  it("导入：提交目录路径，回传最新列表并提示去启用", async () => {
    const onPluginsChanged = vi.fn();
    let importedBody: Record<string, unknown> | null = null;
    stubRoutes((url, method, body) => {
      if (url.endsWith("/plugins") && method === "GET") return jsonResponse(LIST_BODY);
      if (url.endsWith("/plugins/import") && method === "POST") {
        importedBody = body;
        return jsonResponse({
          id: "water-tracker",
          path: "backend/data/plugins/water-tracker",
          enabled: false,
          plugins: [
            makePlugin({
              id: "water-tracker",
              display_name: "喝水记录",
              enabled: false,
              state: "disabled",
            }),
          ],
        });
      }
      return undefined;
    });

    render(<PluginCenter plugins={[makePlugin()]} summary={null} onPluginsChanged={onPluginsChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "添加插件" }));
    fireEvent.change(await screen.findByLabelText(/插件目录路径/), {
      target: { value: "D:/my-plugins/water-tracker" },
    });
    fireEvent.click(screen.getByRole("button", { name: "导入插件" }));

    await waitFor(() => expect(onPluginsChanged).toHaveBeenCalled());
    expect(importedBody).toMatchObject({
      path: "D:/my-plugins/water-tracker",
      replace: false,
    });
    expect(screen.getByRole("status").textContent).toContain("water-tracker");
  });

  it("重新扫描：报告新发现的插件", async () => {
    stubRoutes((url, method) => {
      if (url.endsWith("/plugins") && method === "GET") return jsonResponse(LIST_BODY);
      if (url.endsWith("/plugins/reload") && method === "POST") {
        return jsonResponse({ discovered: ["hand-made"], plugins: [makePlugin()] });
      }
      return undefined;
    });

    render(<PluginCenter plugins={[makePlugin()]} summary={null} />);
    fireEvent.click(screen.getByRole("button", { name: "添加插件" }));
    fireEvent.click(screen.getByRole("button", { name: "重新扫描" }));

    expect((await screen.findByRole("status")).textContent).toContain("hand-made");
  });

  it("导入失败时展示后端原因（不落盘）", async () => {
    stubRoutes((url, method) => {
      if (url.endsWith("/plugins") && method === "GET") return jsonResponse(LIST_BODY);
      if (url.endsWith("/plugins/import") && method === "POST") {
        return jsonResponse({ detail: "目录里没有 manifest.json：D:/nope" }, 400);
      }
      return undefined;
    });

    render(<PluginCenter plugins={[makePlugin()]} summary={null} />);
    fireEvent.click(screen.getByRole("button", { name: "添加插件" }));
    fireEvent.change(await screen.findByLabelText(/插件目录路径/), {
      target: { value: "D:/nope" },
    });
    fireEvent.click(screen.getByRole("button", { name: "导入插件" }));

    expect((await screen.findByRole("alert")).textContent).toContain("manifest.json");
  });

  it("目录信息拿不到时面板仍在（导入 / 重扫两个动作可用）", async () => {
    stubRoutes(() => undefined); // GET /plugins 也 404

    render(<PluginCenter plugins={[makePlugin()]} summary={null} />);
    fireEvent.click(screen.getByRole("button", { name: "添加插件" }));

    expect(await screen.findByLabelText(/插件目录路径/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新扫描" })).toBeTruthy();
  });
});

describe("PluginCenter 交互", () => {
  const SETTINGS_BODY = {
    id: "tavern-bridge",
    values: {},
    schema: {
      type: "object",
      properties: { tavern_dir: { type: "string", title: "酒馆数据目录" } },
    },
    permissions: {
      filesystem: { read: [], write: false, write_paths: [] },
      network: { hosts: [] },
    },
  };

  it("点击插件行展开详情，再点收起（收起时不发请求）", async () => {
    const urls = stubRoutes((url, method) =>
      url.endsWith("/settings") && method === "GET" ? jsonResponse(SETTINGS_BODY) : undefined,
    );

    render(
      <PluginCenter
        plugins={[makePlugin({ id: "tavern-bridge", display_name: "酒馆数据接入" })]}
        summary={makeSummary()}
      />,
    );

    const row = screen.getByRole("button", { name: /酒馆数据接入/ });
    expect(row.getAttribute("aria-expanded")).toBe("false");
    // 面板展开前不该拉配置——插件详情是按需加载的
    expect(urls.filter((url) => url.includes("/settings"))).toHaveLength(0);

    fireEvent.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(await screen.findByLabelText("酒馆数据目录")).toBeTruthy();

    fireEvent.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("false");
    await waitFor(() => expect(screen.queryByLabelText("酒馆数据目录")).toBeNull());
  });

  it("只展开一个插件（手风琴）", async () => {
    stubRoutes((url, method) =>
      url.endsWith("/settings") && method === "GET" ? jsonResponse(SETTINGS_BODY) : undefined,
    );

    render(
      <PluginCenter
        plugins={[
          makePlugin({ id: "a", display_name: "甲插件" }),
          makePlugin({ id: "b", display_name: "乙插件" }),
        ]}
        summary={makeSummary()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /甲插件/ }));
    fireEvent.click(screen.getByRole("button", { name: /乙插件/ }));

    expect(screen.getByRole("button", { name: /甲插件/ }).getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByRole("button", { name: /乙插件/ }).getAttribute("aria-expanded")).toBe("true");
  });

  it("装了酒馆接入插件才渲染记忆导入面板", async () => {
    stubRoutes((url) =>
      url.includes("/status")
        ? jsonResponse({
            root: "/data",
            available: { entries: 1, characters: 1, sessions: 1 },
            warnings: [],
            imported_sessions: [],
            characters: [],
          })
        : undefined,
    );

    const { unmount } = render(
      <PluginCenter
        plugins={[makePlugin({ id: "tavern-bridge" })]}
        summary={makeSummary()}
        companionId="companion-a"
      />,
    );
    expect(await screen.findByText("酒馆记忆导入")).toBeTruthy();
    unmount();

    // 没有酒馆插件时不渲染——不制造一个永远读不到数据的空面板
    render(
      <PluginCenter
        plugins={[makePlugin({ id: "other-plugin" })]}
        summary={makeSummary()}
        companionId="companion-a"
      />,
    );
    expect(screen.queryByText("酒馆记忆导入")).toBeNull();
  });
});
