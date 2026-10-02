/**
 * 技能面板测试（`/skills/*` 的界面层，`AGENTS.md §9.6`）。
 *
 * 重点：
 * 1. **正文按需拉**——展开前不该请求 `/skills/{id}`（正文可能上万 token）；
 * 2. **空技能库仍然渲染**——技能是文件形态的，隐藏面板等于把「往哪放文件」的入口藏了；
 * 3. 启停与展开是两个独立操作——点开关不该顺手把面板展开；
 * 4. **删除要二次确认**（自建技能删了不可恢复），且内置/自建的后果要说清楚；
 * 5. **隐藏项单列一块**并给恢复入口——否则删掉内置技能就再也找不回来了。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SkillPanel } from "@/components/settings/SkillPanel";
import type { SkillInfo } from "@/lib/api/types";

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

function makeSkill(overrides: Partial<SkillInfo> = {}): SkillInfo {
  return {
    id: "crisis-deescalation",
    name: "情绪危机安抚",
    description: "危机信号时的应对流程",
    when_to_use: "用户表达绝望时",
    source: "builtin",
    enabled: true,
    deleted: false,
    body_chars: 820,
    ...overrides,
  };
}

const DETAIL_BODY = {
  id: "crisis-deescalation",
  name: "情绪危机安抚",
  description: "危机信号时的应对流程",
  when_to_use: "用户表达绝望时",
  source: "builtin",
  enabled: true,
  body: "# 情绪危机安抚\n\n第一步：先接住情绪。",
};

describe("SkillPanel 渲染", () => {
  it("空技能库时渲染指路文案（而不是消失）", () => {
    render(<SkillPanel skills={[]} />);

    expect(screen.getByText("技能")).toBeTruthy();
    expect(screen.getByText(/还没有技能/)).toBeTruthy();
    expect(screen.getByText("0/0 启用")).toBeTruthy();
  });

  it("列出技能与启用计数，并标注来源", () => {
    render(
      <SkillPanel
        skills={[
          makeSkill({ id: "a", name: "甲", source: "builtin" }),
          makeSkill({ id: "b", name: "乙", source: "user", enabled: false }),
        ]}
      />,
    );

    expect(screen.getByText("1/2 启用")).toBeTruthy();
    expect(screen.getByText("内置")).toBeTruthy();
    expect(screen.getByText("自建")).toBeTruthy();
  });

  it("展开前不请求正文（正文可能上万 token）", () => {
    const urls = stubRoutes(() => jsonResponse(DETAIL_BODY));

    render(<SkillPanel skills={[makeSkill()]} />);

    expect(urls.length).toBe(0);
  });
});

describe("SkillPanel 展开", () => {
  it("点「820 字」才拉详情并显示正文", async () => {
    const urls = stubRoutes((url, method) =>
      url.includes("/skills/crisis-deescalation") && method === "GET"
        ? jsonResponse(DETAIL_BODY)
        : undefined,
    );

    render(<SkillPanel skills={[makeSkill()]} />);
    fireEvent.click(screen.getByRole("button", { name: "820 字" }));

    expect(await screen.findByText(/第一步：先接住情绪/)).toBeTruthy();
    expect(screen.getByText(/适用场景：用户表达绝望时/)).toBeTruthy();
    expect(urls.filter((url) => url.includes("/crisis-deescalation"))).toHaveLength(1);
  });

  it("再点收起，不重复请求", async () => {
    const urls = stubRoutes(() => jsonResponse(DETAIL_BODY));

    render(<SkillPanel skills={[makeSkill()]} />);
    const toggle = screen.getByRole("button", { name: "820 字" });

    fireEvent.click(toggle);
    await screen.findByText(/第一步：先接住情绪/);

    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    await waitFor(() => expect(screen.queryByText(/第一步：先接住情绪/)).toBeNull());

    // 收起再展开会重新拉一次（内容可能被后端 reload 换掉），但收起本身不发请求
    expect(urls.filter((url) => url.includes("GET") && url.includes("/skills/"))).toHaveLength(1);
  });

  it("详情拉取失败时给出可读错误", async () => {
    stubRoutes(() => jsonResponse({ detail: "技能不存在：x" }, 404));

    render(<SkillPanel skills={[makeSkill()]} />);
    fireEvent.click(screen.getByRole("button", { name: "820 字" }));

    expect((await screen.findByRole("alert")).textContent).toContain("技能不存在：x");
  });
});

describe("SkillPanel 启停", () => {
  it("取消勾选发 POST 并通知页面刷新", async () => {
    const onSkillsChanged = vi.fn();
    const urls = stubRoutes((url, method) =>
      url.endsWith("/enabled") && method === "POST"
        ? jsonResponse({ id: "crisis-deescalation", enabled: false, skills: [] })
        : undefined,
    );

    render(<SkillPanel skills={[makeSkill()]} onSkillsChanged={onSkillsChanged} />);
    fireEvent.click(screen.getByRole("checkbox"));

    await waitFor(() => expect(onSkillsChanged).toHaveBeenCalled());
    const call = urls.find((url) => url.includes("/enabled"));
    expect(call).toContain("POST");
    expect(screen.getByRole("status").textContent).toContain("已禁用「情绪危机安抚」");
  });

  it("启停不会顺手把面板展开", async () => {
    stubRoutes((url, method) =>
      url.endsWith("/enabled") && method === "POST"
        ? jsonResponse({ id: "crisis-deescalation", enabled: true, skills: [] })
        : undefined,
    );

    render(<SkillPanel skills={[makeSkill({ enabled: false })]} />);
    fireEvent.click(screen.getByRole("checkbox"));

    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
    expect(screen.getByRole("button", { name: "820 字" }).getAttribute("aria-expanded")).toBe("false");
  });

  it("启停失败时显示错误", async () => {
    stubRoutes(() => jsonResponse({ detail: "技能不存在：x" }, 404));

    render(<SkillPanel skills={[makeSkill()]} />);
    fireEvent.click(screen.getByRole("checkbox"));

    expect((await screen.findByRole("alert")).textContent).toContain("技能不存在：x");
  });
});

describe("SkillPanel AI 编写技能（§9.12）", () => {
  const DRAFT = {
    id: "calm-down",
    name: "降火",
    description: "先把火气降下来",
    when_to_use: "用户在气头上时",
    body: "# 降火\n\n先复述，再谈事。\n",
  };

  it("生成草稿：填进可编辑表单，但不写磁盘", async () => {
    const urls = stubRoutes((url, method) =>
      url.endsWith("/skills/ai-draft") && method === "POST"
        ? jsonResponse({ draft: DRAFT, model: "qwen-plus" })
        : undefined,
    );

    render(<SkillPanel skills={[makeSkill()]} />);
    fireEvent.click(screen.getByRole("button", { name: "AI 编写技能" }));
    fireEvent.change(screen.getByLabelText(/你想要的能力/), {
      target: { value: "让火气降下来" },
    });
    fireEvent.click(screen.getByRole("button", { name: "AI 生成草稿" }));

    // 草稿进了输入框（可改），且**没有**发生写入
    const idField = (await screen.findByDisplayValue("calm-down")) as HTMLInputElement;
    expect(idField.value).toBe("calm-down");
    expect(screen.getByDisplayValue("降火")).toBeTruthy();
    // stubRoutes 返回的是「方法 URL」字符串列表
    expect(urls).toEqual(["POST http://localhost:8000/skills/ai-draft"]);
  });

  it("保存草稿：把编辑后的内容发给后端并通知刷新", async () => {
    const onSkillsChanged = vi.fn();
    let savedBody: Record<string, unknown> | null = null;
    stubRoutes((url, method, body) => {
      if (url.endsWith("/skills/ai-draft") && method === "POST") {
        return jsonResponse({ draft: DRAFT, model: "qwen-plus" });
      }
      if (url.endsWith("/skills") && method === "POST") {
        savedBody = body;

        return jsonResponse({ skills: [], summary: { total: 3, enabled: 3 }, ...(body as object) });
      }
      return undefined;
    });

    render(<SkillPanel skills={[makeSkill()]} onSkillsChanged={onSkillsChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "AI 编写技能" }));
    fireEvent.change(screen.getByLabelText(/你想要的能力/), { target: { value: "让火气降下来" } });
    fireEvent.click(screen.getByRole("button", { name: "AI 生成草稿" }));
    fireEvent.change(await screen.findByDisplayValue("calm-down"), {
      target: { value: "cool-down" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存技能" }));

    await waitFor(() => expect(onSkillsChanged).toHaveBeenCalled());
    expect(savedBody).not.toBeNull();
    expect((savedBody as unknown as { id: string }).id).toBe("cool-down");
    expect(screen.getByRole("status").textContent).toContain("已保存技能");
  });

  it("模型不可用时把后端的原因原样显示（不静默失败）", async () => {
    stubRoutes((url, method) =>
      url.endsWith("/skills/ai-draft") && method === "POST"
        ? jsonResponse({ detail: "当前用的是本地占位模型（mock）…" }, 400)
        : undefined,
    );

    render(<SkillPanel skills={[makeSkill()]} />);
    fireEvent.click(screen.getByRole("button", { name: "AI 编写技能" }));
    fireEvent.change(screen.getByLabelText(/你想要的能力/), { target: { value: "随便写一个" } });
    fireEvent.click(screen.getByRole("button", { name: "AI 生成草稿" }));

    expect((await screen.findByRole("alert")).textContent).toContain("占位模型");
  });
});

describe("SkillPanel 重新扫描", () => {
  it("重扫后提示统计并通知刷新", async () => {
    const onSkillsChanged = vi.fn();
    const urls = stubRoutes((url, method) =>
      url.endsWith("/skills/reload") && method === "POST"
        ? jsonResponse({ skills: [], summary: { total: 3, enabled: 2 } })
        : undefined,
    );

    render(<SkillPanel skills={[makeSkill()]} onSkillsChanged={onSkillsChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "重新扫描" }));

    await waitFor(() => expect(onSkillsChanged).toHaveBeenCalled());
    expect(screen.getByRole("status").textContent).toContain("共 3 个技能，2 个启用");
    expect(urls.some((url) => url.includes("/skills/reload"))).toBe(true);
  });

  it("重扫不会把隐藏项报成可用技能", async () => {
    stubRoutes((url, method) =>
      url.endsWith("/skills/reload") && method === "POST"
        ? jsonResponse({ skills: [], summary: { total: 2, enabled: 2, hidden: 1 } })
        : undefined,
    );

    render(<SkillPanel skills={[makeSkill()]} />);
    fireEvent.click(screen.getByRole("button", { name: "重新扫描" }));

    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
    expect(screen.getByRole("status").textContent).toContain("另有 1 个已隐藏");
  });
});

describe("SkillPanel 删除（§9.6）", () => {
  const DELETE_OK = {
    deleted: "crisis-deescalation",
    name: "情绪危机安抚",
    source: "builtin",
    hidden: true,
    path: null,
    skills: [],
    summary: { total: 1, enabled: 1, hidden: 1 },
  };

  it("先点删除只是二次确认，确认后才发请求", async () => {
    const onSkillsChanged = vi.fn();
    const urls = stubRoutes((url, method) =>
      url.endsWith("/skills/crisis-deescalation") && method === "DELETE"
        ? jsonResponse(DELETE_OK)
        : undefined,
    );

    render(<SkillPanel skills={[makeSkill()]} onSkillsChanged={onSkillsChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    // 只是展开确认条，还没删
    expect(urls).toEqual([]);
    expect(screen.getByText(/内置技能不会被真正删掉/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(onSkillsChanged).toHaveBeenCalled());
    expect(urls).toEqual(["DELETE http://localhost:8000/skills/crisis-deescalation"]);
    expect(screen.getByRole("status").textContent).toContain("已隐藏「情绪危机安抚」");
  });

  it("取消确认后不发任何请求", async () => {
    const urls = stubRoutes(() => jsonResponse(DELETE_OK));

    render(<SkillPanel skills={[makeSkill()]} />);
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(urls).toEqual([]);
    expect(screen.queryByText(/内置技能不会被真正删掉/)).toBeNull();
  });

  it("自建技能的确认文案说清不可恢复", async () => {
    stubRoutes(() => jsonResponse(DELETE_OK));

    render(<SkillPanel skills={[makeSkill({ source: "user" })]} />);
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    expect(screen.getByText(/删除后无法恢复/)).toBeTruthy();
  });

  it("删除自建技能后提示里带上被删的路径", async () => {
    stubRoutes((url, method) =>
      url.endsWith("/skills/crisis-deescalation") && method === "DELETE"
        ? jsonResponse({
            ...DELETE_OK,
            source: "user",
            hidden: false,
            path: "backend/data/skills/crisis-deescalation",
          })
        : undefined,
    );

    render(<SkillPanel skills={[makeSkill({ source: "user" })]} />);
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
    expect(screen.getByRole("status").textContent).toContain(
      "backend/data/skills/crisis-deescalation",
    );
  });

  it("删除失败时原样显示后端原因", async () => {
    stubRoutes(() => jsonResponse({ detail: "技能不存在：crisis-deescalation" }, 404));

    render(<SkillPanel skills={[makeSkill()]} />);
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    expect((await screen.findByRole("alert")).textContent).toContain("技能不存在");
  });

  it("删掉的技能要跟着收起详情（正文不该继续摆在眼前）", async () => {
    stubRoutes((url, method) => {
      if (url.includes("/skills/crisis-deescalation") && method === "GET") {
        return jsonResponse(DETAIL_BODY);
      }
      if (url.endsWith("/skills/crisis-deescalation") && method === "DELETE") {
        return jsonResponse(DELETE_OK);
      }
      return undefined;
    });

    render(<SkillPanel skills={[makeSkill()]} />);
    fireEvent.click(screen.getByRole("button", { name: "820 字" }));
    await screen.findByText(/第一步：先接住情绪/);

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(screen.queryByText(/第一步：先接住情绪/)).toBeNull());
  });
});

describe("SkillPanel 已隐藏区", () => {
  it("隐藏项单列一块并给出恢复入口", async () => {
    const onSkillsChanged = vi.fn();
    const urls = stubRoutes((url, method) =>
      url.endsWith("/skills/gone/restore") && method === "POST"
        ? jsonResponse({
            restored: "gone",
            name: "旧技能",
            skills: [],
            summary: { total: 2, enabled: 2, hidden: 0 },
          })
        : undefined,
    );

    render(
      <SkillPanel
        skills={[
          makeSkill({ id: "crisis-deescalation", name: "情绪危机安抚" }),
          makeSkill({ id: "gone", name: "旧技能", deleted: true, enabled: false }),
        ]}
        onSkillsChanged={onSkillsChanged}
      />,
    );

    // 隐藏项不占「启用」计数，也不该出现在可见列表里
    expect(screen.getByText("1/1 启用")).toBeTruthy();
    expect(screen.getByText(/已隐藏 1 个内置技能/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "恢复" }));

    await waitFor(() => expect(onSkillsChanged).toHaveBeenCalled());
    expect(urls).toEqual(["POST http://localhost:8000/skills/gone/restore"]);
    expect(screen.getByRole("status").textContent).toContain("已恢复「旧技能」");
  });

  it("全部技能都被隐藏时仍然渲染面板（不显示「还没有技能」）", () => {
    render(<SkillPanel skills={[makeSkill({ deleted: true, enabled: false })]} />);

    expect(screen.queryByText(/还没有技能/)).toBeNull();
    expect(screen.getByText(/已隐藏 1 个内置技能/)).toBeTruthy();
    expect(screen.getByText("0/0 启用")).toBeTruthy();
  });

  it("恢复失败时原样显示后端原因", async () => {
    stubRoutes(() => jsonResponse({ detail: "技能不在隐藏清单里：gone" }, 404));

    render(<SkillPanel skills={[makeSkill({ id: "gone", deleted: true })]} />);
    fireEvent.click(screen.getByRole("button", { name: "恢复" }));

    expect((await screen.findByRole("alert")).textContent).toContain("不在隐藏清单");
  });
});
