/**
 * AI 适配面板的交互测试：试运行 → 展示前后对照 → 用户确认后应用。
 *
 * fetch 全部 mock（按 URL/方法路由成响应），不发真实请求，也不依赖后端与真实模型。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StPresetAdaptPanel } from "@/components/studio/StPresetAdaptPanel";
import type { StAdaptResult, StPresetDetail } from "@/lib/api/types";

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

function makeDetail(): StPresetDetail {
  return {
    id: "demo",
    sampling: { temperature: 0.8, max_tokens: 1024 },
    extended_sampling: {},
    assembly: {
      use_sysprompt: false,
      squash_system_messages: false,
      names_behavior: 0,
      show_thoughts: false,
    },
    order: [],
    prompts: [],
    memory_injection: {
      enabled: true,
      position: "in_chat",
      depth: 1,
      role: "system",
      order: 100,
    },
    override: {},
    system_prompt_override: "",
    source_format: "chat",
    stripped_keys: [],
    warnings: [],
    unsupported: [],
  };
}

function makePlan(overrides: Partial<StAdaptResult> = {}): StAdaptResult {
  return {
    preset_id: "demo",
    dry_run: true,
    applied: false,
    model_used: true,
    summary: { high: 2, medium: 1, low: 0 },
    findings: [
      {
        rule_id: "thinking_enabled",
        severity: "high",
        action: "auto_patch",
        scope: "preset",
        identifier: "",
        name: "show_thoughts",
        evidence: "show_thoughts = True",
        message: "（测试语料）预设开启了推理模式",
        anchor: "",
      },
      {
        rule_id: "third_person_directive",
        severity: "high",
        action: "llm_rewrite",
        scope: "content",
        identifier: "person-rule",
        name: "人称准则",
        evidence: "（测试语料）第三人称",
        message: "（测试语料）要求全程第三人称叙事",
        anchor: "",
      },
    ],
    preserved: [
      {
        rule_id: "nsfw_content",
        severity: "high",
        action: "preserve",
        scope: "content",
        identifier: "nsfw-rule",
        name: "NSFW 强化",
        evidence: "（测试语料）NSFW",
        message: "（测试语料）内容保持原样，仅建议归拢到 nsfw 槽位附近",
        anchor: "nsfw",
      },
    ],
    rewrites: [
      {
        identifier: "person-rule",
        name: "人称准则",
        status: "applied",
        reason: "",
        new_content: "（测试语料）改用第二人称。",
      },
    ],
    diff: [
      {
        scope: "preset",
        identifier: "",
        name: "",
        field: "show_thoughts",
        field_label: "推理模式",
        before: true,
        after: false,
      },
      {
        scope: "item",
        identifier: "template-rule",
        name: "输出模板",
        field: "enabled",
        field_label: "启用",
        before: true,
        after: false,
      },
      {
        scope: "order",
        identifier: "",
        name: "",
        field: "prompt_order",
        field_label: "注入顺序",
        before: null,
        after: null,
        moved: [
          { identifier: "nsfw-rule", name: "NSFW 强化", from_index: 7, to_index: 2 },
        ],
      },
    ],
    patch: { assembly: { show_thoughts: false } },
    warnings: ["（测试语料）条目「人称准则」改动幅度较大，建议重点检查"],
    ...overrides,
  };
}

function renderPanel(onApplied = vi.fn()) {
  render(<StPresetAdaptPanel presetId="demo" onApplied={onApplied} />);
  return onApplied;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("StPresetAdaptPanel", () => {
  it("先看建议：走试运行，展示前后对照与保护说明", async () => {
    const calls = stubRoutes(() => jsonResponse(makePlan()));
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "先看建议" }));

    await screen.findByText("推理模式");
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toContain("/chat/st-presets/demo/ai-adapt");
    expect(calls[0].body).toMatchObject({ dry_run: true });   // 试运行不得落盘

    // 三种对照都要出现：预设字段 / 条目开关 / 注入顺序
    expect(screen.getByText("输出模板 · 启用")).toBeTruthy();
    expect(screen.getByText("注入顺序")).toBeTruthy();
    expect(screen.getByText(/NSFW 强化：第 8 位 → 第 3 位/)).toBeTruthy();

    // 被保护的条目要明说「内容保持原样」
    expect(screen.getByText(/内容保持原样/)).toBeTruthy();
  });

  it("应用：直接把刚确认的补丁写入覆盖层（不重跑适配）", async () => {
    const detail = makeDetail();
    const calls = stubRoutes((url, method) => {
      if (url.includes("/ai-adapt")) return jsonResponse(makePlan());
      if (method === "PATCH" && url.includes("/chat/st-presets/demo")) {
        return jsonResponse({ detail });
      }
      return undefined;
    });
    const onApplied = renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "先看建议" }));
    await screen.findByText("推理模式");

    fireEvent.click(screen.getByRole("button", { name: "应用这些修改" }));

    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1));
    expect(onApplied.mock.calls[0][0]).toMatchObject({ id: "demo" });
    // 关键：只发一次适配请求（试运行），落盘走 PATCH 且用的就是那份补丁
    expect(calls).toHaveLength(2);
    expect(calls[1].method).toBe("PATCH");
    expect(calls[1].body).toMatchObject({ assembly: { show_thoughts: false } });
    expect(screen.getByRole("status").textContent).toContain("已应用");
    // 应用后收起建议区，避免用户对着过期对照做决定
    expect(screen.queryByText("推理模式")).toBeNull();
  });

  it("切换预设：上一个预设的建议立即失效", async () => {
    stubRoutes(() => jsonResponse(makePlan()));
    const { rerender } = render(<StPresetAdaptPanel presetId="a" onApplied={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "先看建议" }));
    await screen.findByText("推理模式");

    // 父组件切换预设：不改 key 也不该串用旧建议（否则会把 A 的对照写到 B 上）
    rerender(<StPresetAdaptPanel presetId="b" onApplied={vi.fn()} />);

    expect(screen.queryByText("推理模式")).toBeNull();
    expect(screen.getByRole("button", { name: "先看建议" })).toBeTruthy();
  });

  it("取消：丢弃建议，不写任何东西", async () => {
    const calls = stubRoutes(() => jsonResponse(makePlan()));
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "先看建议" }));
    await screen.findByText("推理模式");

    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(screen.queryByText("推理模式")).toBeNull();
    expect(calls).toHaveLength(1);                             // 没有第二次请求
    expect(screen.getByRole("button", { name: "先看建议" })).toBeTruthy();
  });

  it("没有可改项时禁用应用按钮", async () => {
    stubRoutes(() =>
      jsonResponse(
        makePlan({ diff: [], summary: { high: 0, medium: 0, low: 0 }, patch: {} }),
      ),
    );
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "先看建议" }));

    await screen.findByText("没有需要改动的地方。");
    const applyButton = screen.getByRole("button", { name: "应用这些修改" }) as HTMLButtonElement;
    expect(applyButton.disabled).toBe(true);
  });

  it("模型未参与时给出明确提示（避免用户以为改写过了）", async () => {
    stubRoutes(() => jsonResponse(makePlan({ model_used: false, rewrites: [] })));
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "先看建议" }));

    await screen.findByText(/模型未参与/);
  });

  it("失败：提示可读错误，且不误报成功", async () => {
    stubRoutes(() => jsonResponse({ detail: "（测试语料）模型不可用" }, 500));
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "先看建议" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("（测试语料）模型不可用");
  });

  it("对话进行中禁用入口按钮", () => {
    stubRoutes(() => jsonResponse(makePlan()));
    render(<StPresetAdaptPanel presetId="demo" onApplied={vi.fn()} disabled />);

    expect(
      (screen.getByRole("button", { name: "先看建议" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
