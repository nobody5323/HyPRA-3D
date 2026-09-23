/**
 * 对话模型设置面板测试（`/llm/*` 的界面层）。
 *
 * 重点验证三件事：
 * 1. **key 永不回显**：打开面板只回填 provider / 端点 / 模型，key 输入框始终为空，
 *    留空提交时必须是 `null`（等于「沿用已保存的 key」）；
 * 2. 切 provider 自动带出该家的默认端点，避免用户手打长 URL；
 * 3. 「测试连接」不改变当前配置（后端已保证，前端也要走独立请求）。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LlmSettings } from "@/components/settings/LlmSettings";

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

const CONFIG_BODY = {
  config: {
    provider: "openai-compatible",
    base_url: "https://example.invalid/v1",
    model: "demo-model",
    timeout: 60,
    enable_thinking: null,
    has_api_key: true,
    source: "runtime",
  },
  providers: [
    { value: "mock", label: "本地占位（mock）", hint: "无需 key" },
    { value: "dashscope", label: "阿里百炼", hint: "百炼默认端点" },
    { value: "openai-compatible", label: "自定义端点", hint: "需填 Base URL" },
  ],
  default_base_urls: {
    dashscope: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  },
};

function valueOf(label: string): string {
  return (screen.getByLabelText(label) as HTMLInputElement | HTMLSelectElement).value;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("LlmSettings：读取与回填", () => {
  it("打开后拉取配置并回填 provider / 端点 / 模型", async () => {
    stubRoutes((url, method) =>
      url.endsWith("/llm/config") && method === "GET"
        ? jsonResponse(CONFIG_BODY)
        : undefined,
    );

    render(<LlmSettings open onClose={() => {}} />);

    await waitFor(() => expect(valueOf("模型")).toBe("demo-model"));
    expect(valueOf("服务商")).toBe("openai-compatible");
    expect(valueOf("Base URL（端点）")).toBe("https://example.invalid/v1");
    // key 明文只在后端：输入框始终为空，placeholder 说明「留空则沿用」
    expect(valueOf("API Key")).toBe("");
    expect((screen.getByLabelText("API Key") as HTMLInputElement).placeholder).toContain(
      "沿用",
    );
  });

  it("显示当前来源（界面设置 / 部署配置）", async () => {
    stubRoutes((url, method) =>
      url.endsWith("/llm/config") && method === "GET"
        ? jsonResponse(CONFIG_BODY)
        : undefined,
    );

    render(<LlmSettings open onClose={() => {}} />);

    await waitFor(() => expect(screen.getByText("界面设置（已存本机）")).toBeTruthy());
    expect(screen.getByText(/当前：demo-model/)).toBeTruthy();
  });

  it("后端离线时给出明确提示，而不是空白面板", async () => {
    stubRoutes(() => jsonResponse({ detail: "boom" }, 500));

    render(<LlmSettings open onClose={() => {}} />);

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("读不到当前模型配置"),
    );
  });

  it("切换服务商会自动填入该家的默认端点", async () => {
    stubRoutes((url, method) =>
      url.endsWith("/llm/config") && method === "GET"
        ? jsonResponse(CONFIG_BODY)
        : undefined,
    );

    render(<LlmSettings open onClose={() => {}} />);
    await waitFor(() => expect(valueOf("服务商")).toBe("openai-compatible"));

    fireEvent.change(screen.getByLabelText("服务商"), { target: { value: "dashscope" } });

    expect(valueOf("Base URL（端点）")).toBe(
      "https://dashscope.aliyuncs.com/compatible-mode/v1",
    );
  });
});

describe("LlmSettings：模型列表与测试连接", () => {
  it("拉取列表成功后提示数量（并带上当前表单的端点）", async () => {
    const calls = stubRoutes((url, method) => {
      if (url.includes("/llm/models")) return jsonResponse({ models: ["a-model", "b-model"] });
      if (url.endsWith("/llm/config") && method === "GET") return jsonResponse(CONFIG_BODY);
      return undefined;
    });

    render(<LlmSettings open onClose={() => {}} />);
    await waitFor(() => expect(valueOf("模型")).toBe("demo-model"));

    fireEvent.click(screen.getByRole("button", { name: "拉取列表" }));

    await waitFor(() => expect(screen.getByText(/拉到 2 个模型/)).toBeTruthy());
    const request = calls.find((call) => call.url.includes("/llm/models"));
    expect(request?.url).toContain("base_url=https%3A%2F%2Fexample.invalid%2Fv1");
  });

  it("拉取失败时把后端原文显示出来", async () => {
    stubRoutes((url, method) => {
      if (url.includes("/llm/models")) return jsonResponse({ detail: "拉取模型列表失败：401" }, 400);
      if (url.endsWith("/llm/config") && method === "GET") return jsonResponse(CONFIG_BODY);
      return undefined;
    });

    render(<LlmSettings open onClose={() => {}} />);
    await waitFor(() => expect(valueOf("模型")).toBe("demo-model"));

    fireEvent.click(screen.getByRole("button", { name: "拉取列表" }));

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("拉取模型列表失败"),
    );
  });

  it("测试连接成功显示延迟；失败显示错误", async () => {
    let ok = true;
    stubRoutes((url, method) => {
      if (url.includes("/llm/config/test")) {
        return jsonResponse(
          ok
            ? { ok: true, latency_ms: 123, model: "demo-model", provider: "openai-compatible" }
            : { ok: false, latency_ms: 5, model: "demo-model", error: "401 Unauthorized" },
        );
      }
      if (url.endsWith("/llm/config") && method === "GET") return jsonResponse(CONFIG_BODY);
      return undefined;
    });

    render(<LlmSettings open onClose={() => {}} />);
    await waitFor(() => expect(valueOf("模型")).toBe("demo-model"));

    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
    await waitFor(() => expect(screen.getByText(/连接成功（123 ms/)).toBeTruthy());

    ok = false;
    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("401 Unauthorized"),
    );
  });

  it("测试连接不得触发 PUT（不能因为点了一下测试就换了模型）", async () => {
    const calls = stubRoutes((url, method) => {
      if (url.includes("/llm/config/test")) {
        return jsonResponse({ ok: true, latency_ms: 10, model: "demo-model" });
      }
      if (url.endsWith("/llm/config") && method === "GET") return jsonResponse(CONFIG_BODY);
      return undefined;
    });

    render(<LlmSettings open onClose={() => {}} />);
    await waitFor(() => expect(valueOf("模型")).toBe("demo-model"));

    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/test"))).toBe(true));

    expect(calls.some((call) => call.method === "PUT")).toBe(false);
  });
});

describe("LlmSettings：应用与恢复", () => {
  it("应用：key 留空时提交 null（沿用已保存的），并回调新配置", async () => {
    const onApplied = vi.fn();
    const calls = stubRoutes((url, method) => {
      if (url.endsWith("/llm/config") && method === "PUT") {
        return jsonResponse({
          config: { ...CONFIG_BODY.config, model: "new-model", source: "runtime" },
        });
      }
      if (url.endsWith("/llm/config") && method === "GET") return jsonResponse(CONFIG_BODY);
      return undefined;
    });

    render(<LlmSettings open onClose={() => {}} onApplied={onApplied} />);
    await waitFor(() => expect(valueOf("模型")).toBe("demo-model"));

    fireEvent.change(screen.getByLabelText("模型"), { target: { value: "new-model" } });
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    await waitFor(
      () => expect(calls.some((call) => call.method === "PUT")).toBe(true),
      { timeout: 3000 },
    );
    const put = calls.find((call) => call.method === "PUT");
    expect(put?.body).toMatchObject({ model: "new-model", api_key: null, persist: true });
    await waitFor(() => expect(onApplied).toHaveBeenCalled(), { timeout: 3000 });
    expect(onApplied.mock.calls[0][0]).toMatchObject({ model: "new-model" });
  });

  it("填入新 key 时原样提交（前端不回显旧值）", async () => {
    const calls = stubRoutes((url, method) => {
      if (url.endsWith("/llm/config") && method === "PUT") {
        return jsonResponse({ config: CONFIG_BODY.config });
      }
      if (url.endsWith("/llm/config") && method === "GET") return jsonResponse(CONFIG_BODY);
      return undefined;
    });

    render(<LlmSettings open onClose={() => {}} />);
    await waitFor(() => expect(valueOf("模型")).toBe("demo-model"));

    fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "sk-new" } });
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    await waitFor(
      () => expect(calls.some((call) => call.method === "PUT")).toBe(true),
      { timeout: 3000 },
    );
    expect(calls.find((call) => call.method === "PUT")?.body).toMatchObject({
      api_key: "sk-new",
    });
  });

  it("应用失败时显示后端错误原文", async () => {
    stubRoutes((url, method) => {
      if (url.endsWith("/llm/config") && method === "PUT") {
        return jsonResponse({ detail: "缺少 API Key" }, 400);
      }
      if (url.endsWith("/llm/config") && method === "GET") return jsonResponse(CONFIG_BODY);
      return undefined;
    });

    render(<LlmSettings open onClose={() => {}} />);
    await waitFor(() => expect(valueOf("模型")).toBe("demo-model"));

    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    await waitFor(
      () => expect(screen.getByRole("alert").textContent).toContain("缺少 API Key"),
      { timeout: 3000 },
    );
  });

  it("恢复部署配置会调用 DELETE 并回填 .env 的值", async () => {
    const calls = stubRoutes((url, method) => {
      if (url.endsWith("/llm/config") && method === "DELETE") {
        return jsonResponse({
          removed: true,
          config: {
            ...CONFIG_BODY.config,
            provider: "mock",
            model: "mock",
            source: "env",
            has_api_key: false,
          },
        });
      }
      if (url.endsWith("/llm/config") && method === "GET") return jsonResponse(CONFIG_BODY);
      return undefined;
    });

    render(<LlmSettings open onClose={() => {}} />);
    await waitFor(() => expect(valueOf("模型")).toBe("demo-model"));

    fireEvent.click(screen.getByRole("button", { name: "恢复部署配置" }));

    await waitFor(
      () => expect(calls.some((call) => call.method === "DELETE")).toBe(true),
      { timeout: 3000 },
    );
    await waitFor(() => expect(valueOf("模型")).toBe("mock"), { timeout: 3000 });
    expect(screen.getByText("来自部署配置 .env")).toBeTruthy();
  });
});

describe("LlmSettings：无障碍", () => {
  it("是模态对话框，Esc 会关闭", async () => {
    stubRoutes((url, method) =>
      url.endsWith("/llm/config") && method === "GET"
        ? jsonResponse(CONFIG_BODY)
        : undefined,
    );
    const onClose = vi.fn();

    render(<LlmSettings open onClose={onClose} />);
    await waitFor(() => expect(valueOf("模型")).toBe("demo-model"));

    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("关闭按钮可用", async () => {
    stubRoutes((url, method) =>
      url.endsWith("/llm/config") && method === "GET"
        ? jsonResponse(CONFIG_BODY)
        : undefined,
    );
    const onClose = vi.fn();

    render(<LlmSettings open onClose={onClose} />);
    await waitFor(() => expect(valueOf("模型")).toBe("demo-model"));

    fireEvent.click(screen.getByRole("button", { name: "关闭" }));

    expect(onClose).toHaveBeenCalled();
  });

  it("未打开时不渲染", () => {
    const { container } = render(<LlmSettings open={false} onClose={() => {}} />);
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });
});
