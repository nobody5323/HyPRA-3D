/**
 * 文风工坊的交互测试：加载 / 内置只读 / 新建 / 本地校验 / 示例对话增删。
 *
 * fetch 全部 mock（按 URL + 方法路由成响应），不发真实请求，也不依赖后端。
 * 覆盖的关键行为：
 * - 清单列出内置与自建文风，并标出「几组示例」；
 * - **内置文风不可编辑但可复制 / 删除**：表单置灰；
 * - 必填与采样区间在**本地**拦住（不为一个越界温度打后端）；
 * - 示例对话成对提交，半截示例不会发出去；
 * - 新建成功后回调带回刷新过的 catalog（页面据此刷新文风选择器）。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StyleStudio } from "@/components/studio/StyleStudio";
import type {
  StudioCatalog,
  StudioSamplingSpec,
  StudioStyle,
  StudioStyleSummary,
} from "@/lib/api/types";

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

const SAMPLING_SPEC: StudioSamplingSpec[] = [
  { key: "temperature", min: 0, max: 2 },
  { key: "max_tokens", min: 1, max: 8192 },
];

function makeStyleSummary(
  overrides: Partial<StudioStyleSummary> = {},
): StudioStyleSummary {
  return {
    id: "brief-direct",
    name: "简短利落",
    description: "一次只说一两句",
    tags: ["简短"],
    example_count: 3,
    builtin: true,
    ...overrides,
  };
}

function makeStyle(overrides: Partial<StudioStyle> = {}): StudioStyle {
  return {
    ...makeStyleSummary(),
    style_prompt: "惜字。一次只回应一两句。",
    avoid: ["长篇分析"],
    examples: [{ user: "我最近总是失眠", assistant: "有几天了？" }],
    sampling: { temperature: 0.8 },
    conflicts_with: ["健谈"],
    ...overrides,
  };
}

function makeCatalog(overrides: Partial<StudioCatalog> = {}): StudioCatalog {
  return {
    personas: [],
    entries: [],
    styles: [makeStyleSummary()],
    warnings: [],
    limits: { style_name: 40, style_prompt: 4000, style_description: 200, style_examples: 6 },
    sampling_spec: SAMPLING_SPEC,
    scope_all: "*",
    state_vars: [],
    ...overrides,
  };
}

function renderStudio(catalog: StudioCatalog = makeCatalog()) {
  const onCatalog = vi.fn();
  render(<StyleStudio catalog={catalog} onCatalog={onCatalog} />);
  return { onCatalog };
}

/** 表单里的输入框没有稳定的 label 关联，用 placeholder 定位（与其它工坊测试同一做法） */
function fieldByPlaceholder(
  text: string | RegExp,
): HTMLInputElement | HTMLTextAreaElement {
  return screen.getByPlaceholderText(text) as HTMLInputElement | HTMLTextAreaElement;
}

describe("StyleStudio", () => {
  it("列出文风并标出示例组数", async () => {
    stubRoutes((url) => {
      if (url.endsWith("/chat/studio/styles/brief-direct")) {
        return jsonResponse({ style: makeStyle() });
      }
      return undefined;
    });

    renderStudio();

    // 清单项（左栏）
    expect(screen.getByText("文风（1）")).toBeTruthy();
    expect(screen.getByText("3 组示例")).toBeTruthy();
    // 详情自动选中第一份并回填表单
    await waitFor(() => {
      expect(fieldByPlaceholder("例如：深夜电台").value).toBe("简短利落");
    });
    expect(fieldByPlaceholder("例如：深夜电台").disabled).toBe(true); // 内置只读
  });

  it("内置文风只能复制或删除，没有保存按钮", async () => {
    stubRoutes((url) => {
      if (url.endsWith("/chat/studio/styles/brief-direct")) {
        return jsonResponse({ style: makeStyle() });
      }
      return undefined;
    });

    renderStudio();

    await waitFor(() => expect(screen.getByText("复制为我的文风")).toBeTruthy());
    expect(screen.queryByText("保存修改")).toBeNull();
    expect(screen.getByText("删除文风")).toBeTruthy();
    expect(screen.getByText(/这是内置文风/)).toBeTruthy();
  });

  it("新建文风：提交清洗后的字段，并回调刷新 catalog", async () => {
    const created = makeStyle({
      id: "user-abc",
      name: "深夜电台",
      builtin: false,
      example_count: 1,
      examples: [{ user: "睡不着", assistant: "那就别急着睡。我在。" }],
      sampling: { temperature: 0.6 },
    });

    const calls = stubRoutes((url, method) => {
      if (url.endsWith("/chat/studio/styles") && method === "POST") {
        return jsonResponse({ style: created, catalog: makeCatalog({ styles: [] }) });
      }
      return undefined;
    });

    const { onCatalog } = renderStudio(makeCatalog({ styles: [] }));

    fireEvent.click(screen.getByText("新建"));
    fireEvent.change(fieldByPlaceholder("例如：深夜电台"), {
      target: { value: "深夜电台" },
    });
    fireEvent.change(fieldByPlaceholder(/例如：低声说话/), {
      target: { value: "低声说话，句子短。" },
    });
    // 第一组示例：只填一半时应当被本地校验拦住
    fireEvent.change(fieldByPlaceholder("我最近总是失眠"), {
      target: { value: "睡不着" },
    });
    fireEvent.click(screen.getByText("创建文风"));
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toContain("第 1 组示例对话");
    });
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);

    // 补齐另一半后再提交
    fireEvent.change(fieldByPlaceholder("有几天了？"), {
      target: { value: "那就别急着睡。我在。" },
    });
    fireEvent.click(screen.getByText("创建文风"));

    await waitFor(() => expect(onCatalog).toHaveBeenCalledTimes(1));
    const post = calls.find((call) => call.method === "POST");
    expect(post?.body).toMatchObject({
      name: "深夜电台",
      style_prompt: "低声说话，句子短。",
      examples: [{ user: "睡不着", assistant: "那就别急着睡。我在。" }],
      // 没填的采样参数不会被带上（留空 = 不指定）
      sampling: {},
    });
  });

  it("采样参数越界在本地拦住（不为一个错误量级打后端）", async () => {
    const calls = stubRoutes((url, method) => {
      if (url.endsWith("/chat/studio/styles") && method === "POST") {
        return jsonResponse({ style: makeStyle({ builtin: false }), catalog: makeCatalog() });
      }
      return undefined;
    });

    renderStudio(makeCatalog({ styles: [] }));
    fireEvent.click(screen.getByText("新建"));
    fireEvent.change(fieldByPlaceholder("例如：深夜电台"), { target: { value: "越界" } });
    fireEvent.change(fieldByPlaceholder(/例如：低声说话/), {
      target: { value: "低声说话。" },
    });
    // temperature 的 placeholder 是区间文案，用它定位输入框
    fireEvent.change(screen.getByPlaceholderText("0 ~ 2"), { target: { value: "80" } });

    fireEvent.click(screen.getByText("创建文风"));
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toContain("温度 应在 0 ~ 2 之间");
    });
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
  });

  it("示例对话可增可删，且至少留一组", async () => {
    stubRoutes((url) => {
      // 用**自建**文风：内置文风的表单整体置灰，增删按钮本来就该点不动
      if (url.endsWith("/chat/studio/styles/user-abc")) {
        return jsonResponse({
          style: makeStyle({ id: "user-abc", name: "我的文风", builtin: false }),
        });
      }
      return undefined;
    });

    renderStudio(
      makeCatalog({ styles: [makeStyleSummary({ id: "user-abc", name: "我的文风", builtin: false })] }),
    );
    await waitFor(() => expect(screen.getByText("加一组")).toBeTruthy());

    fireEvent.click(screen.getByText("加一组"));
    await waitFor(() => expect(screen.getAllByText("删掉这组")).toHaveLength(2));

    fireEvent.click(screen.getAllByText("删掉这组")[0]);
    await waitFor(() => expect(screen.getAllByText("删掉这组")).toHaveLength(1));
    // 删到最后一组时不再减少（界面上要留一个落点）
    fireEvent.click(screen.getAllByText("删掉这组")[0]);
    await waitFor(() => expect(screen.getAllByText("删掉这组")).toHaveLength(1));
  });

  it("删除文风时二次确认，且明确说明不影响对话与记忆", async () => {
    const calls = stubRoutes((url, method) => {
      if (url.endsWith("/chat/studio/styles/user-abc") && method === "GET") {
        return jsonResponse({ style: makeStyle({ id: "user-abc", builtin: false }) });
      }
      if (url.endsWith("/chat/studio/styles/user-abc") && method === "DELETE") {
        return jsonResponse({ deleted: "user-abc", name: "深夜电台", catalog: makeCatalog() });
      }
      return undefined;
    });

    const { onCatalog } = renderStudio(
      makeCatalog({ styles: [makeStyleSummary({ id: "user-abc", builtin: false })] }),
    );

    await waitFor(() => expect(screen.getByText("删除文风")).toBeTruthy());
    fireEvent.click(screen.getByText("删除文风"));
    expect(screen.getByText(/对话与记忆不受影响/)).toBeTruthy();

    fireEvent.click(screen.getByText("确认删除"));
    await waitFor(() => expect(onCatalog).toHaveBeenCalledTimes(1));
    expect(calls.some((call) => call.method === "DELETE")).toBe(true);
  });
});
