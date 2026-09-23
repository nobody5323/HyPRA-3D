/**
 * 技能面板测试（`/skills/*` 的界面层，`AGENTS.md §9.6`）。
 *
 * 重点：
 * 1. **正文按需拉**——展开前不该请求 `/skills/{id}`（正文可能上万 token）；
 * 2. **空技能库仍然渲染**——技能是文件形态的，隐藏面板等于把「往哪放文件」的入口藏了；
 * 3. 启停与展开是两个独立操作——点开关不该顺手把面板展开。
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
});
