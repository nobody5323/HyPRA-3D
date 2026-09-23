/**
 * 酒馆记忆导入面板测试（P4 的界面层）。
 *
 * 重点：
 * 1. **未就绪不是故障**——插件没启用时后端返回 409，界面要指路而不是弹错误框；
 * 2. **重复导入安全**——已导入的会话由后端跳过，所以主按钮可以随便点；
 * 3. **重新导入要二次确认**——`force=true` 会重复写入记忆，不能一键直达。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TavernImportPanel } from "@/components/settings/TavernImportPanel";

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

function stubRoutes(handler: (url: string, method: string) => Response | undefined): string[] {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      urls.push(`${method} ${url}`);
      return handler(url, method) ?? jsonResponse({ detail: "not found" }, 404);
    }),
  );
  return urls;
}

const STATUS_BODY = {
  root: "D:/SillyTavern/data/default-user",
  available: { entries: 12, characters: 3, sessions: 5 },
  warnings: [],
  imported_sessions: [] as string[],
  characters: ["测试角色"],
};

const IMPORT_BODY = {
  companion_id: "companion-a",
  sessions_total: 5,
  sessions_imported: 4,
  sessions_skipped: 1,
  turns: 20,
  facts: 7,
  memories: 20,
  warnings: [],
};

describe("TavernImportPanel", () => {
  it("插件未生效（409）时给指路文案，而且不弹错误框", async () => {
    const urls = stubRoutes(() =>
      jsonResponse({ detail: "酒馆接入插件尚未生效：请先在能力中心启用它并填写酒馆数据目录" }, 409),
    );

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText(/尚未生效：请先启用「酒馆数据接入」/)).toBeTruthy();
    // 这是「还没轮到这一步」，不是失败——不该出现 alert
    expect(screen.queryByRole("alert")).toBeNull();
    expect(urls.length).toBe(1);
  });

  it("读取成功时显示数据目录与内容计数", async () => {
    stubRoutes((url) => (url.includes("/status") ? jsonResponse(STATUS_BODY) : undefined));

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText("D:/SillyTavern/data/default-user")).toBeTruthy();
    expect(screen.getByText("世界书条目 12 · 角色卡 3 · 会话 5")).toBeTruthy();
    expect(screen.getByText(/已导入到当前陪伴对象/)).toBeTruthy();
  });

  it("点「导入记忆」用 force=false，并把统计显示出来", async () => {
    const urls = stubRoutes((url) => {
      if (url.includes("/status")) return jsonResponse(STATUS_BODY);
      if (url.includes("/import")) return jsonResponse(IMPORT_BODY);
      return undefined;
    });

    render(<TavernImportPanel companionId="companion-a" />);
    fireEvent.click(await screen.findByRole("button", { name: "导入记忆" }));

    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
    expect(screen.getByText(/已导入 4 个会话（跳过 1）/)).toBeTruthy();
    expect(screen.getByText(/7 条事实/)).toBeTruthy();

    const importCall = urls.find((url) => url.includes("/import"));
    expect(importCall).toContain("force=false");
    expect(importCall).toContain("companion_id=companion-a");
  });

  it("有已导入记录时，「重新导入」需二次确认才发 force=true", async () => {
    const urls = stubRoutes((url) => {
      if (url.includes("/status")) {
        return jsonResponse({ ...STATUS_BODY, imported_sessions: ["s1", "s2"] });
      }
      if (url.includes("/import")) return jsonResponse(IMPORT_BODY);
      return undefined;
    });

    render(<TavernImportPanel companionId="companion-a" />);

    fireEvent.click(await screen.findByRole("button", { name: "重新导入…" }));
    // 第一次点击只是「武装」确认态，不能直接导入——否则误点就重复写入记忆
    expect(urls.some((url) => url.includes("/import"))).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /确认重新导入 2 个会话/ }));

    await waitFor(() => expect(urls.some((url) => url.includes("/import"))).toBe(true));
    expect(urls.find((url) => url.includes("/import"))).toContain("force=true");
  });

  it("后端故障（500）才算错误，走 alert", async () => {
    stubRoutes(() => jsonResponse({ detail: "记忆服务未就绪" }, 500));

    render(<TavernImportPanel companionId="companion-a" />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("记忆服务未就绪");
  });

  it("没有选中陪伴对象时不发请求，并说明原因", () => {
    const urls = stubRoutes(() => jsonResponse(STATUS_BODY));

    render(<TavernImportPanel companionId="" />);

    expect(screen.getByText(/请先选择一个陪伴对象/)).toBeTruthy();
    expect(urls.length).toBe(0);
  });
});
