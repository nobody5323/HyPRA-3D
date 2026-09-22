/**
 * 酒馆预设面板的交互测试：清单 / 选择 / 开关 / 排序 / 记忆注入 / 未生效提示。
 *
 * fetch 全部 mock（按 URL + 方法路由成响应），不发真实请求，也不依赖后端。
 * 覆盖层写回是**防抖**的，因此断言要 `waitFor`（默认 1s 不够，显式放宽）。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StPresetPanel } from "@/components/StPresetPanel";
import type { StPresetDetail, StPresetSummary } from "@/lib/types";

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
  body: unknown;
}

/** 按 URL/方法路由的 fetch 替身；返回全部调用记录供断言。 */
function stubRoutes(
  handler: (url: string, method: string, body: unknown) => Response | undefined,
): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ url, method, body });
      return handler(url, method, body) ?? jsonResponse({ detail: "not found" }, 404);
    }),
  );
  return calls;
}

function makeSummary(overrides: Partial<StPresetSummary> = {}): StPresetSummary {
  return {
    id: "demo",
    name: "演示预设",
    source_file: "demo.json",
    imported_at: "2026-03-04T10:00:00+00:00",
    prompt_count: 3,
    enabled_count: 2,
    sampling: { temperature: 0.66 },
    warnings: [],
    unsupported: [],
    has_override: false,
    sha256: "abc",
    ...overrides,
  };
}

function makeDetail(overrides: Partial<StPresetDetail> = {}): StPresetDetail {
  return {
    id: "demo",
    sampling: { temperature: 0.66 },
    extended_sampling: { top_k: 40 },
    assembly: { use_sysprompt: false, squash_system_messages: true, names_behavior: 0 },
    order: [
      { identifier: "main", enabled: true },
      { identifier: "chatHistory", enabled: true },
      { identifier: "mood-note", enabled: false },
    ],
    prompts: [
      {
        identifier: "main",
        name: "Main",
        role: "system",
        content: "（测试语料）以角色身份回应。",
        marker: false,
        system_prompt: true,
        forbid_overrides: false,
        injection_position: 0,
        injection_depth: 4,
        injection_order: 100,
        injection_trigger: [],
        marker_source: "",
        content_editable: true,
      },
      {
        identifier: "chatHistory",
        name: "Chat History",
        role: "system",
        content: "",
        marker: true,
        system_prompt: true,
        forbid_overrides: false,
        injection_position: 0,
        injection_depth: 4,
        injection_order: 100,
        injection_trigger: [],
        marker_source: "滚动窗口历史 + 本次输入",
        content_editable: false,
      },
      {
        identifier: "mood-note",
        name: "情绪提醒",
        role: "user",
        content: "（测试语料）回应放慢一些。",
        marker: false,
        system_prompt: false,
        forbid_overrides: false,
        injection_position: 1,
        injection_depth: 2,
        injection_order: 50,
        injection_trigger: [],
        marker_source: "",
        content_editable: true,
      },
    ],
    memory_injection: { enabled: true, position: "in_chat", depth: 1, role: "system", order: 100 },
    override: {},
    system_prompt_override: "",
    source_format: "chat",
    stripped_keys: [],
    warnings: [],
    unsupported: [],
    ...overrides,
  };
}

/** 展开面板（面板默认收起，避免无谓请求）。 */
function expand() {
  fireEvent.click(screen.getByRole("button", { name: /酒馆预设/ }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("预设面板：清单与选择", () => {
  it("展开后才拉清单，并列出已导入的预设", async () => {
    const calls = stubRoutes((url, method) => {
      if (url.endsWith("/chat/st-presets") && method === "GET") {
        return jsonResponse({ dir: "/tmp/presets", presets: [makeSummary()] });
      }
      return undefined;
    });

    render(<StPresetPanel value="" onChange={() => {}} />);

    // 收起状态不发请求
    expect(calls).toHaveLength(0);

    expand();

    await waitFor(() => expect(screen.getByText("演示预设")).toBeTruthy());
    expect(screen.getByText("不使用酒馆预设（走内置分层组装）")).toBeTruthy();
  });

  it("没有导入任何预设时给出明确空态", async () => {
    stubRoutes((url, method) =>
      url.endsWith("/chat/st-presets") && method === "GET"
        ? jsonResponse({ dir: "/tmp/presets", presets: [] })
        : undefined,
    );

    render(<StPresetPanel value="" onChange={() => {}} />);
    expand();

    await waitFor(() => expect(screen.getByText("还没有导入任何预设。")).toBeTruthy());
  });

  it("选择预设会回调 onChange", async () => {
    const onChange = vi.fn();
    stubRoutes((url, method) => {
      if (url.endsWith("/chat/st-presets") && method === "GET") {
        return jsonResponse({ dir: "/tmp/presets", presets: [makeSummary()] });
      }
      return undefined;
    });

    render(<StPresetPanel value="" onChange={onChange} />);
    expand();

    await waitFor(() => expect(screen.getByLabelText(/演示预设/)).toBeTruthy());
    fireEvent.click(screen.getByLabelText(/演示预设/));

    expect(onChange).toHaveBeenCalledWith("demo");
  });

  it("已选中的预设会加载详情并渲染编辑器", async () => {
    stubRoutes((url, method) => {
      if (url.endsWith("/chat/st-presets/demo") && method === "GET") {
        return jsonResponse({ preset: makeSummary(), detail: makeDetail() });
      }
      if (url.endsWith("/chat/st-presets") && method === "GET") {
        return jsonResponse({ dir: "/tmp/presets", presets: [makeSummary()] });
      }
      return undefined;
    });

    render(<StPresetPanel value="demo" onChange={() => {}} />);
    expand();

    await waitFor(() => expect(screen.getByText("提示词条目")).toBeTruthy());
    expect(screen.getByText("Main")).toBeTruthy();
    // 未生效清单为空时不渲染该区块
    expect(screen.queryByText("该预设中本项目不会生效的部分")).toBeNull();
  });

  it("未生效特性与兼容处理都会显示出来", async () => {
    stubRoutes((url, method) => {
      if (url.endsWith("/chat/st-presets/demo") && method === "GET") {
        return jsonResponse({
          preset: makeSummary(),
          detail: makeDetail({
            unsupported: ["群聊专有设置「group_nudge_prompt」在本项目不生效"],
            warnings: ["预设未提供条目顺序，已按条目定义顺序生成（全部启用）"],
          }),
        });
      }
      if (url.endsWith("/chat/st-presets") && method === "GET") {
        return jsonResponse({ dir: "/tmp/presets", presets: [makeSummary()] });
      }
      return undefined;
    });

    render(<StPresetPanel value="demo" onChange={() => {}} />);
    expand();

    await waitFor(() =>
      expect(screen.getByText("该预设中本项目不会生效的部分")).toBeTruthy(),
    );
    expect(screen.getByText("导入时的兼容处理")).toBeTruthy();
  });
});

describe("预设编辑：写回覆盖层", () => {
  function renderSelected(calls: Call[]) {
    render(<StPresetPanel value="demo" onChange={() => {}} />);
    expand();
    return calls;
  }

  function routeWithDetail() {
    return stubRoutes((url, method) => {
      if (url.endsWith("/chat/st-presets/demo") && method === "PATCH") {
        return jsonResponse({ detail: makeDetail() });
      }
      if (url.endsWith("/chat/st-presets/demo") && method === "GET") {
        return jsonResponse({ preset: makeSummary(), detail: makeDetail() });
      }
      if (url.endsWith("/chat/st-presets") && method === "GET") {
        return jsonResponse({ dir: "/tmp/presets", presets: [makeSummary()] });
      }
      return undefined;
    });
  }

  it("关闭某个条目会提交该条目的 enabled=false", async () => {
    const calls = renderSelected(routeWithDetail());
    await waitFor(() => expect(screen.getByLabelText("启用条目 Main")).toBeTruthy());

    fireEvent.click(screen.getByLabelText("启用条目 Main"));

    await waitFor(
      () => {
        const patch = calls.find((call) => call.method === "PATCH");
        expect(patch?.body).toMatchObject({ prompts: { main: { enabled: false } } });
      },
      { timeout: 3000 },
    );
  });

  it("上移条目会提交新的 prompt_order", async () => {
    const calls = renderSelected(routeWithDetail());
    await waitFor(() => expect(screen.getByLabelText("上移 Chat History")).toBeTruthy());

    fireEvent.click(screen.getByLabelText("上移 Chat History"));

    await waitFor(
      () => {
        const patch = calls.find((call) => call.method === "PATCH");
        expect(patch?.body).toMatchObject({
          prompt_order: ["chatHistory", "main", "mood-note"],
        });
      },
      { timeout: 3000 },
    );
  });

  it("编辑正文只提交该条目的 content（占位条目不提供文本框）", async () => {
    const calls = renderSelected(routeWithDetail());
    await waitFor(() => expect(screen.getByText("Main")).toBeTruthy());

    // 展开 Main
    fireEvent.click(screen.getByText("Main"));
    const textarea = screen.getByLabelText("正文");
    fireEvent.change(textarea, { target: { value: "改过的正文" } });

    await waitFor(
      () => {
        const patch = calls.find((call) => call.method === "PATCH");
        expect(patch?.body).toMatchObject({ prompts: { main: { content: "改过的正文" } } });
      },
      { timeout: 3000 },
    );
  });

  it("「全部启用」一次性打开所有条目（社区大预设动辄 100+ 条）", async () => {
    const calls = renderSelected(routeWithDetail());
    await waitFor(() => expect(screen.getByRole("button", { name: "全部启用" })).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "全部启用" }));

    await waitFor(
      () => {
        const patch = calls.find((call) => call.method === "PATCH");
        expect(patch?.body).toMatchObject({
          prompts: {
            main: { enabled: true },
            chatHistory: { enabled: true },
            "mood-note": { enabled: true },
          },
        });
      },
      { timeout: 3000 },
    );
  });

  it("「全部关闭」同样一次到位", async () => {
    const calls = renderSelected(routeWithDetail());
    await waitFor(() => expect(screen.getByRole("button", { name: "全部关闭" })).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "全部关闭" }));

    await waitFor(
      () => {
        const patch = calls.find((call) => call.method === "PATCH");
        expect(patch?.body).toMatchObject({ prompts: { main: { enabled: false } } });
      },
      { timeout: 3000 },
    );
  });

  it("组装开关（合并系统消息）能提交到 assembly", async () => {
    const calls = renderSelected(routeWithDetail());
    await waitFor(() =>
      expect(screen.getByLabelText("合并相邻的系统消息")).toBeTruthy(),
    );

    // 与夹具当前值解耦：断言提交的是「点击后的状态」
    const checkbox = screen.getByLabelText("合并相邻的系统消息") as HTMLInputElement;
    const before = checkbox.checked;
    fireEvent.click(checkbox);

    await waitFor(
      () => {
        const patch = calls.find((call) => call.method === "PATCH");
        expect(patch?.body).toMatchObject({
          assembly: { squash_system_messages: !before },
        });
      },
      { timeout: 3000 },
    );
  });

  it("use_sysprompt 开启后可填写统一系统指令并提交", async () => {
    const calls = stubRoutes((url, method) => {
      if (url.endsWith("/chat/st-presets/demo") && method === "PATCH") {
        return jsonResponse({ detail: makeDetail() });
      }
      if (url.endsWith("/chat/st-presets/demo") && method === "GET") {
        return jsonResponse({
          preset: makeSummary(),
          detail: makeDetail({
            assembly: { use_sysprompt: true, squash_system_messages: false, names_behavior: 0 },
          }),
        });
      }
      if (url.endsWith("/chat/st-presets") && method === "GET") {
        return jsonResponse({ dir: "/tmp/presets", presets: [makeSummary()] });
      }
      return undefined;
    });

    render(<StPresetPanel value="demo" onChange={() => {}} />);
    expand();
    await waitFor(() => expect(screen.getByLabelText("统一系统指令")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("统一系统指令"), {
      target: { value: "（测试语料）统一指令" },
    });

    await waitFor(
      () => {
        const patch = calls.find((call) => call.method === "PATCH");
        expect(patch?.body).toMatchObject({ system_prompt_override: "（测试语料）统一指令" });
      },
      { timeout: 3000 },
    );
  });

  it("占位条目明确提示正文由运行时填充，不给输入框", async () => {
    renderSelected(routeWithDetail());
    await waitFor(() => expect(screen.getByText("Chat History")).toBeTruthy());

    fireEvent.click(screen.getByText("Chat History"));

    expect(screen.getByText(/占位条目：正文由运行时填充/)).toBeTruthy();
  });

  it("采样参数：滑块改动会提交新值，「恢复」提交 null 回到预设原值", async () => {
    const calls = renderSelected(routeWithDetail());
    await waitFor(() => expect(screen.getByLabelText("温度 temperature 滑块")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("温度 temperature 滑块"), {
      target: { value: "1.2" },
    });
    await waitFor(
      () => {
        expect(calls.some((call) => call.method === "PATCH")).toBe(true);
      },
      { timeout: 3000 },
    );
    const firstPatch = calls.find((call) => call.method === "PATCH");
    expect(firstPatch?.body).toMatchObject({ sampling: { temperature: 1.2 } });

    fireEvent.click(screen.getByLabelText("恢复温度 temperature的预设原值"));
    await waitFor(
      () => {
        const patches = calls.filter((call) => call.method === "PATCH");
        expect(patches[patches.length - 1]?.body).toMatchObject({
          sampling: { temperature: null },
        });
      },
      { timeout: 3000 },
    );
  });

  it("记忆注入位置可改，且默认值来自预设详情", async () => {
    const calls = renderSelected(routeWithDetail());
    await waitFor(() => expect(screen.getByLabelText("位置")).toBeTruthy());

    const select = screen.getByLabelText("位置") as HTMLSelectElement;
    expect(select.value).toBe("in_chat");

    fireEvent.change(select, { target: { value: "in_prompt" } });

    await waitFor(
      () => {
        const patch = calls.find((call) => call.method === "PATCH");
        expect(patch?.body).toMatchObject({ memory_injection: { position: "in_prompt" } });
      },
      { timeout: 3000 },
    );
  });
});

describe("预设管理：导入 / 删除 / 导出", () => {
  it("选择文件后按内容导入，并自动选中新预设", async () => {
    const onChange = vi.fn();
    const calls = stubRoutes((url, method) => {
      if (url.includes("/chat/st-presets/import") && method === "POST") {
        return jsonResponse({
          preset: makeSummary(),
          detail: makeDetail(),
        });
      }
      if (url.endsWith("/chat/st-presets") && method === "GET") {
        return jsonResponse({ dir: "/tmp/presets", presets: [makeSummary()] });
      }
      if (url.endsWith("/chat/st-presets/demo") && method === "GET") {
        return jsonResponse({ preset: makeSummary(), detail: makeDetail() });
      }
      return undefined;
    });

    render(<StPresetPanel value="" onChange={onChange} />);
    expand();

    const file = new File(['{"prompts": []}'], "demo.json", { type: "application/json" });
    // jsdom 的 File 没有 text()：补一个（组件正是这么读文件的）
    Object.defineProperty(file, "text", { value: async () => '{"prompts": []}' });

    const input = screen.getByLabelText<HTMLInputElement>("选择预设文件");
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(
      () => {
        const posted = calls.find((call) => call.url.includes("/import"));
        expect(posted?.body).toMatchObject({ content: '{"prompts": []}', source_file: "demo.json" });
      },
      { timeout: 3000 },
    );
    await waitFor(() => expect(onChange).toHaveBeenCalledWith("demo"), { timeout: 3000 });
  });

  it("删除需要二次确认，确认后调用 DELETE 并清空选择", async () => {
    const onChange = vi.fn();
    const calls = stubRoutes((url, method) => {
      if (url.endsWith("/chat/st-presets/demo") && method === "DELETE") {
        return jsonResponse({ deleted: "demo" });
      }
      if (url.endsWith("/chat/st-presets/demo") && method === "GET") {
        return jsonResponse({ preset: makeSummary(), detail: makeDetail() });
      }
      if (url.endsWith("/chat/st-presets") && method === "GET") {
        return jsonResponse({ dir: "/tmp/presets", presets: [makeSummary()] });
      }
      return undefined;
    });

    render(<StPresetPanel value="demo" onChange={onChange} />);
    expand();
    // 用 label 定位列表项：折叠标题里也会出现预设名，getByText 会命中两个
    await waitFor(() => expect(screen.getByLabelText(/演示预设/)).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(screen.getByText("确认删除？")).toBeTruthy();
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(
      () => expect(calls.some((call) => call.method === "DELETE")).toBe(true),
      { timeout: 3000 },
    );
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(""), { timeout: 3000 });
  });

  it("导出会请求导出接口（下载本身交给浏览器）", async () => {
    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    Object.assign(URL, { createObjectURL, revokeObjectURL });

    const calls = stubRoutes((url, method) => {
      if (url.includes("/export") && method === "GET") {
        return jsonResponse({ temperature: 0.66 });
      }
      if (url.endsWith("/chat/st-presets") && method === "GET") {
        return jsonResponse({ dir: "/tmp/presets", presets: [makeSummary()] });
      }
      return undefined;
    });

    try {
      render(<StPresetPanel value="" onChange={() => {}} />);
      expand();
      await waitFor(() => expect(screen.getByText("演示预设")).toBeTruthy());

      fireEvent.click(screen.getByRole("button", { name: "导出" }));

      await waitFor(
        () => expect(calls.some((call) => call.url.includes("/export"))).toBe(true),
        { timeout: 3000 },
      );
      await waitFor(() => expect(createObjectURL).toHaveBeenCalled(), { timeout: 3000 });
    } finally {
      Object.assign(URL, {
        createObjectURL: originalCreate,
        revokeObjectURL: originalRevoke,
      });
    }
  });

  it("导入失败时把后端错误原文显示出来（role=alert）", async () => {
    stubRoutes((url, method) => {
      if (url.includes("/import") && method === "POST") {
        return jsonResponse({ detail: "预设解析失败：预设不是合法的 JSON" }, 400);
      }
      if (url.endsWith("/chat/st-presets") && method === "GET") {
        return jsonResponse({ dir: "/tmp/presets", presets: [] });
      }
      return undefined;
    });

    render(<StPresetPanel value="" onChange={() => {}} />);
    expand();

    const file = new File(["{ not json"], "broken.json", { type: "application/json" });
    Object.defineProperty(file, "text", { value: async () => "{ not json" });
    fireEvent.change(screen.getByLabelText<HTMLInputElement>("选择预设文件"), {
      target: { files: [file] },
    });

    await waitFor(
      () => expect(screen.getByRole("alert").textContent).toContain("预设解析失败"),
      { timeout: 3000 },
    );
  });
});
