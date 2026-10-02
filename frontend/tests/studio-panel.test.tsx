/**
 * 创作工坊面板的交互测试：加载 / 内置只读 / 新建角色 / 本地校验 / 世界书试触发。
 *
 * fetch 全部 mock（按 URL + 方法路由成响应），不发真实请求，也不依赖后端。
 * 覆盖的关键行为：
 * - catalog 一次加载后列出角色与世界书条目；
 * - **内置资源不可编辑但可删除**：表单置灰，可复制或从当前工坊删除；
 * - 必填校验在**本地**拦住（不为一个空名字打后端）；
 * - 「试触发」把当前表单当草稿提交，并把逐通道结论显示出来。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StudioPanel } from "@/components/studio/StudioPanel";
import type {
  StudioCatalog,
  StudioEntryTestResult,
  StudioPersona,
  StudioPersonaSummary,
  StudioWorldBookEntry,
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

function makePersonaSummary(
  overrides: Partial<StudioPersonaSummary> = {},
): StudioPersonaSummary {
  return {
    id: "therapist-elder-sister",
    name: "苏澄",
    title: "会倾听的邻家姐姐",
    description: "温和的陪伴者",
    tags: ["温柔"],
    builtin: true,
    ...overrides,
  };
}

function makePersona(overrides: Partial<StudioPersona> = {}): StudioPersona {
  return {
    ...makePersonaSummary(),
    prompt: "你是{{user_name}}的朋友。",
    background: "",
    variables: ["user_name"],
    creator: "hypra-original",
    ...overrides,
  };
}

function makeEntry(overrides: Partial<StudioWorldBookEntry> = {}): StudioWorldBookEntry {
  return {
    id: "pet-cat",
    title: "苏澄的猫「团子」",
    scope: "*",
    content: "苏澄养了一只叫「团子」的三花猫。",
    keys: ["猫"],
    regex: [],
    vector_text: "",
    vector_threshold: 0.7,
    enabled: true,
    case_sensitive: false,
    priority: 5,
    builtin: true,
    ...overrides,
  };
}

function makeCatalog(overrides: Partial<StudioCatalog> = {}): StudioCatalog {
  return {
    personas: [makePersonaSummary()],
    entries: [makeEntry()],
    styles: [],
    warnings: [],
    limits: { persona_name: 50, persona_prompt: 8000, persona_background: 8000 },
    sampling_spec: [
      { key: "temperature", min: 0, max: 2 },
      { key: "max_tokens", min: 1, max: 8192 },
    ],
    scope_all: "*",
    state_vars: [
      { name: "user_name", description: "用户称呼", default: "朋友", example: "小林" },
    ],
    ...overrides,
  };
}

const TEST_RESULT: StudioEntryTestResult = {
  matched: true,
  keys_hit: ["猫"],
  regex_hit: [],
  vector_score: null,
  vector_threshold: 0.7,
  scope: "*",
  injected_text: "[苏澄的猫「团子」]\n苏澄养了一只叫「团子」的三花猫。",
  estimated_tokens: 24,
  warnings: [],
};

function renderPanel() {
  const onClose = vi.fn();
  const onCatalogChange = vi.fn();
  render(
    <StudioPanel
      open
      onClose={onClose}
      defaultScope="therapist-elder-sister"
      onCatalogChange={onCatalogChange}
    />,
  );
  return { onClose, onCatalogChange };
}

describe("StudioPanel", () => {
  it("打开时加载 catalog，并列出角色与世界书条目", async () => {
    stubRoutes((url, method) => {
      if (url.endsWith("/chat/studio/catalog") && method === "GET") {
        return jsonResponse(makeCatalog());
      }
      if (url.endsWith("/chat/studio/personas/therapist-elder-sister")) {
        return jsonResponse({ persona: makePersona() });
      }
      return undefined;
    });

    renderPanel();

    expect(await screen.findByText("苏澄")).toBeTruthy();
    // 「内置」徽标：角色列表里有一个
    expect(screen.getAllByText("内置").length).toBeGreaterThan(0);
    // 角色清单不含正文，正文要单独取详情 → textarea 里应出现详情里的正文
    await waitFor(() => {
      expect(screen.getByDisplayValue("你是{{user_name}}的朋友。")).toBeTruthy();
    });
  });

  it("内置角色不可编辑，但可以复制或删除", async () => {
    stubRoutes((url, method) => {
      if (url.endsWith("/chat/studio/catalog") && method === "GET") {
        return jsonResponse(makeCatalog());
      }
      if (url.endsWith("/chat/studio/personas/therapist-elder-sister")) {
        return jsonResponse({ persona: makePersona() });
      }
      return undefined;
    });

    renderPanel();

    const nameInput = await screen.findByDisplayValue("苏澄");
    expect((nameInput as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "复制为我的角色" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "删除角色卡" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "保存修改" })).toBeNull();
  });

  it("新建角色：提交后调用创建接口并回显新 id", async () => {
    const calls = stubRoutes((url, method) => {
      if (url.endsWith("/chat/studio/catalog") && method === "GET") {
        return jsonResponse(makeCatalog());
      }
      if (url.endsWith("/chat/studio/personas/therapist-elder-sister")) {
        return jsonResponse({ persona: makePersona() });
      }
      if (url.endsWith("/chat/studio/personas") && method === "POST") {
        return jsonResponse({
          persona: makePersona({
            id: "user-xiaolan",
            name: "小岸",
            builtin: false,
            prompt: "你是我的伙伴。",
          }),
          catalog: makeCatalog({
            personas: [
              makePersonaSummary(),
              makePersonaSummary({ id: "user-xiaolan", name: "小岸", builtin: false }),
            ],
          }),
        });
      }
      return undefined;
    });

    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "新建" }));
    fireEvent.change(screen.getByPlaceholderText("例如：陆星野"), {
      target: { value: "小岸" },
    });
    fireEvent.change(screen.getByPlaceholderText(/你是\{\{user_name\}\}的朋友/), {
      target: { value: "你是我的伙伴。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建角色" }));

    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === "POST" && call.url.endsWith("/chat/studio/personas"),
      );
      expect(posted).toBeTruthy();
      expect(posted?.body).toMatchObject({ name: "小岸", prompt: "你是我的伙伴。" });
    });
    expect(await screen.findByText(/角色已创建/)).toBeTruthy();
  });

  it("角色名为空时本地拦截，不打后端", async () => {
    const calls = stubRoutes((url, method) => {
      if (url.endsWith("/chat/studio/catalog") && method === "GET") {
        return jsonResponse(makeCatalog());
      }
      if (url.endsWith("/chat/studio/personas/therapist-elder-sister")) {
        return jsonResponse({ persona: makePersona() });
      }
      return undefined;
    });

    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "新建" }));
    fireEvent.click(screen.getByRole("button", { name: "创建角色" }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("角色名不能为空");
    expect(
      calls.some((call) => call.method === "POST" && call.url.endsWith("/chat/studio/personas")),
    ).toBe(false);
  });

  it("世界书 tab 的「试触发」：提交当前草稿并显示逐通道结论", async () => {
    const calls = stubRoutes((url, method) => {
      if (url.endsWith("/chat/studio/catalog") && method === "GET") {
        return jsonResponse(makeCatalog());
      }
      if (url.endsWith("/chat/studio/personas/therapist-elder-sister")) {
        return jsonResponse({ persona: makePersona() });
      }
      if (url.endsWith("/chat/studio/worldbook/test") && method === "POST") {
        return jsonResponse(TEST_RESULT);
      }
      return undefined;
    });

    renderPanel();

    fireEvent.click(await screen.findByRole("tab", { name: /世界书/ }));
    const sample = await screen.findByLabelText("试触发的样例文本");
    fireEvent.change(sample, { target: { value: "我家的猫很黏人" } });
    fireEvent.click(screen.getByRole("button", { name: "试触发" }));

    await waitFor(() => {
      const posted = calls.find((call) =>
        call.url.endsWith("/chat/studio/worldbook/test"),
      );
      expect(posted).toBeTruthy();
      expect(posted?.body).toMatchObject({ text: "我家的猫很黏人", keys: ["猫"] });
    });
    expect(await screen.findByText("会触发这条设定")).toBeTruthy();
    expect(screen.getByText(/关键词命中：猫/)).toBeTruthy();
  });

  it("catalog 的读取警告会展示给用户（手改坏的文件不该静默消失）", async () => {
    stubRoutes((url, method) => {
      if (url.endsWith("/chat/studio/catalog") && method === "GET") {
        return jsonResponse(
          makeCatalog({ warnings: ["角色文件 user-broken.yaml 解析失败，已跳过：语法错误"] }),
        );
      }
      if (url.endsWith("/chat/studio/personas/therapist-elder-sister")) {
        return jsonResponse({ persona: makePersona() });
      }
      return undefined;
    });

    renderPanel();

    expect(await screen.findByText(/user-broken\.yaml/)).toBeTruthy();
  });
});

describe("PersonaStudio AI 补全（§9.12）", () => {
  const DRAFT_BODY = {
    draft: {
      name: "苏澄",
      title: "会听人说话的姐姐",
      description: "话不多，但总能接住你的情绪。",
      tags: ["温柔", "稳重"],
      prompt: "你是苏澄，先接住情绪再谈事情。",
      background: "你在海边长大。",
      variables: ["user_name"],
    },
    model: "qwen-plus",
  };

  it("生成草稿：只填表单，不发保存请求", async () => {
    const calls = stubRoutes((url, method) => {
      if (url.endsWith("/chat/studio/catalog") && method === "GET") {
        return jsonResponse(makeCatalog());
      }
      if (url.endsWith("/chat/studio/personas/ai-draft") && method === "POST") {
        return jsonResponse(DRAFT_BODY);
      }
      if (url.endsWith("/chat/studio/personas/therapist-elder-sister")) {
        return jsonResponse({ persona: makePersona() });
      }
      return undefined;
    });

    renderPanel();
    await screen.findByDisplayValue("你是{{user_name}}的朋友。");

    // 内置角色不可编辑（AI 补全也跟着锁住），所以先进入「新建」态
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    fireEvent.change(screen.getByLabelText(/你想要的角色/), {
      target: { value: "想做一个话少的姐姐" },
    });
    fireEvent.click(screen.getByRole("button", { name: "AI 补全" }));

    // 草稿被填进了表单（而不是直接成了一个新角色）
    expect(await screen.findByDisplayValue("会听人说话的姐姐")).toBeTruthy();
    expect(screen.getByDisplayValue(/你是苏澄/)).toBeTruthy();
    expect(
      calls.some((call) => call.method === "POST" && call.url.endsWith("/chat/studio/personas")),
    ).toBe(false);
  });

  it("模型不可用时把原因显示在面板里", async () => {
    stubRoutes((url, method) => {
      if (url.endsWith("/chat/studio/catalog") && method === "GET") {
        return jsonResponse(makeCatalog());
      }
      if (url.endsWith("/chat/studio/personas/ai-draft") && method === "POST") {
        return jsonResponse({ detail: "当前用的是本地占位模型（mock）…" }, 400);
      }
      if (url.endsWith("/chat/studio/personas/therapist-elder-sister")) {
        return jsonResponse({ persona: makePersona() });
      }
      return undefined;
    });

    renderPanel();
    await screen.findByDisplayValue("你是{{user_name}}的朋友。");

    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    fireEvent.change(screen.getByLabelText(/你想要的角色/), { target: { value: "随便" } });
    fireEvent.click(screen.getByRole("button", { name: "AI 补全" }));

    expect((await screen.findByRole("alert")).textContent).toContain("占位模型");
  });
});
