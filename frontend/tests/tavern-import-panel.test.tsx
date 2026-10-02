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
  worldbook_enabled: false,
  pending_turns: 0,
  pending_sessions: 0,
  knowledge: { documents: 0, chunks: 0, scopes: 0 },
  // `books` 是**完整清单**（后端 `book_catalog()` 不受勾选影响），
  // 所以它和筛选后的 `available.entries` 对得上；断言「没有世界书」的用例
  // 必须把它也清空，只改 `available` 是自相矛盾的夹具。
  books: [{ source: "world/甲", label: "世界书·甲", entries: 12, enabled: true }],
};

const IMPORT_BODY = {
  companion_id: "companion-a",
  sessions_total: 5,
  sessions_imported: 4,
  sessions_skipped: 1,
  turns: 20,
  facts: 7,
  memories: 20,
  scopes: { "tbp-aaa": 20 },
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
    // 状态只读一次（面板还会自拉一份人设清单，那是另一回事）
    expect(urls.filter((line) => line.includes("/status")).length).toBe(1);
  });

  it("读取成功时显示数据目录与内容计数", async () => {
    stubRoutes((url) => (url.includes("/status") ? jsonResponse(STATUS_BODY) : undefined));

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText("D:/SillyTavern/data/default-user")).toBeTruthy();
    expect(screen.getByText("世界书条目 12 · 角色卡 3 · 会话 5")).toBeTruthy();
    expect(screen.getByText(/已导入到当前陪伴对象|当前陪伴对象已导入/)).toBeTruthy();
  });

  it("读到世界书时说明只按需从知识库召回，不做整本直注入", async () => {
    stubRoutes((url) => (url.includes("/status") ? jsonResponse(STATUS_BODY) : undefined));

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText(/不会整本直接写入提示词/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "同步到知识库" })).toBeTruthy();
  });

  it("没读到世界书条目时不提示注入开关", async () => {
    stubRoutes((url) =>
      url.includes("/status")
        ? jsonResponse({ ...STATUS_BODY, available: { characters: 1, sessions: 0 }, books: [] })
        : undefined,
    );

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText("角色卡 1 · 会话 0")).toBeTruthy();
    expect(screen.queryByText(/世界书设定/)).toBeNull();
  });

  it("有待同步内容时显示轮数与会话数", async () => {
    // 数字来自后端与导入共用的同一套游标规则，所以“说有 3 轮”就是“点下去写 3 轮”
    stubRoutes((url) =>
      url.includes("/status")
        ? jsonResponse({ ...STATUS_BODY, pending_turns: 7, pending_sessions: 2 })
        : undefined,
    );

    render(<TavernImportPanel companionId="companion-a" />);

    expect(
      await screen.findByText(/有 7 轮新对话待同步（分布在 2 个会话里）/),
    ).toBeTruthy();
  });

  it("没有待同步内容时说已是最新", async () => {
    stubRoutes((url) => (url.includes("/status") ? jsonResponse(STATUS_BODY) : undefined));

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText(/记忆已是最新/)).toBeTruthy();
  });

  it("说明「同步新对话」与「重新导入」的区别", async () => {
    stubRoutes((url) => (url.includes("/status") ? jsonResponse(STATUS_BODY) : undefined));

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText(/已同步过的轮次会被跳过/)).toBeTruthy();
    expect(screen.getByText(/从头重写（会重复写入记忆）/)).toBeTruthy();
  });

  it("世界书走知识库这条路时显示规模并能触发同步", async () => {
    // 两条路必须分开说：注入占每轮预算，知识库按需召回
    const urls = stubRoutes((url) => {
      if (url.includes("/knowledge/sync")) {
        return jsonResponse({
          root: "D:/SillyTavern",
          documents: 15,
          chunks: 400,
          removed: 1,
          skipped: 0,
          scopes: { "*": 11 },
          warnings: [],
        });
      }
      if (url.includes("/status")) {
        return jsonResponse({
          ...STATUS_BODY,
          knowledge: { documents: 14, chunks: 380, scopes: 5 },
        });
      }
      return undefined;
    });

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText(/世界书已入知识库：14 篇 \/ 380 条设定/)).toBeTruthy();
    expect(screen.getByText(/按需召回：问到相关的事才进上下文/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "同步到知识库" }));

    await waitFor(() => expect(screen.getByText(/已写入 15 篇 \/ 400 条设定/)).toBeTruthy());
    expect(screen.getByText(/清理 1 篇失效来源/)).toBeTruthy();
    expect(urls.some((url) => url.includes("/knowledge/sync"))).toBe(true);
  });

  it("没读到世界书条目时不显示知识库那一段", async () => {
    stubRoutes((url) =>
      url.includes("/status")
        ? jsonResponse({ ...STATUS_BODY, available: { characters: 1, sessions: 0 }, books: [] })
        : undefined,
    );

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText("角色卡 1 · 会话 0")).toBeTruthy();
    expect(screen.queryByText(/世界书已入知识库/)).toBeNull();
  });

  it("列出可选世界书，取消勾选时把新的禁用列表写回配置", async () => {
    const books = [
      { source: "world/甲", label: "世界书·甲", entries: 12, enabled: true },
      { source: "char/乙", label: "角色内嵌设定·乙", entries: 3, enabled: false },
    ];
    const urls: string[] = [];
    let saved: Record<string, unknown> | null = null;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        urls.push(`${init?.method ?? "GET"} ${url}`);
        if (url.includes("/settings") && init?.method === "PUT") {
          saved = (JSON.parse(String(init.body)) as { values: Record<string, unknown> }).values;
          return jsonResponse({ id: "tavern-bridge", values: saved, secrets_set: [] });
        }
        if (url.includes("/settings")) {
          // 配置里本来就有别的键（tavern_dir 等）：只提交一个键会把它们抹掉
          return jsonResponse({
            id: "tavern-bridge",
            values: { tavern_dir: "D:/SillyTavern", tavern_disabled_books: ["char/乙"] },
            secrets_set: [],
          });
        }
        if (url.includes("/status")) return jsonResponse({ ...STATUS_BODY, books });
        return jsonResponse({});
      }),
    );

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText(/参与接入的世界书/)).toBeTruthy();
    const unchecked = screen.getByRole("checkbox", { name: /角色内嵌设定·乙/ });
    expect((unchecked as HTMLInputElement).checked).toBe(false);

    fireEvent.click(screen.getByRole("checkbox", { name: /世界书·甲/ }));

    await waitFor(() => expect(saved).not.toBeNull());
    // 取消勾选甲 → 它进禁用列表；乙原本就在里面
    expect((saved as unknown as { tavern_disabled_books: string[] }).tavern_disabled_books).toEqual([
      "char/乙",
      "world/甲",
    ]);
    // 其余配置键原样带回去，没被抹掉
    expect((saved as unknown as { tavern_dir: string }).tavern_dir).toBe("D:/SillyTavern");
    expect(urls.some((line) => line.startsWith("PUT"))).toBe(true);
  });

  it("读到的世界书条目为空时不显示勾选区", async () => {
    stubRoutes((url) =>
      url.includes("/status")
        ? jsonResponse({ ...STATUS_BODY, available: { characters: 1, sessions: 0 }, books: [] })
        : undefined,
    );

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText("角色卡 1 · 会话 0")).toBeTruthy();
    expect(screen.queryByText(/参与接入的世界书/)).toBeNull();
  });

  it("点「同步新对话」用 force=false，并把统计显示出来", async () => {
    const urls = stubRoutes((url) => {
      if (url.includes("/status")) return jsonResponse(STATUS_BODY);
      if (url.includes("/import")) return jsonResponse(IMPORT_BODY);
      return undefined;
    });

    render(<TavernImportPanel companionId="companion-a" />);
    fireEvent.click(await screen.findByRole("button", { name: "同步新对话" }));

    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
    expect(screen.getByText(/已导入 4 个会话（跳过 1）/)).toBeTruthy();
    expect(screen.getByText(/7 条事实/)).toBeTruthy();

    const importCall = urls.find((url) => url.includes("/import"));
    expect(importCall).toContain("force=false");
    expect(importCall).toContain("companion_id=companion-a");
  });

  it("说明酒馆里不同角色的剧情各进各的陪伴对象", async () => {
    // 宿主按角色分开写记忆（§8.2 的隔离）：不说清的话，用户在别的角色下
    // 召不回刚导的剧情，只会以为功能坏了
    stubRoutes((url) => (url.includes("/status") ? jsonResponse(STATUS_BODY) : undefined));

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText(/不同角色的剧情会各进各的陪伴对象/)).toBeTruthy();
    expect(screen.getByText(/当前陪伴对象已导入/)).toBeTruthy();
  });

  it("写进了多个陪伴对象时在结果里说明", async () => {
    stubRoutes((url) => {
      if (url.includes("/status")) return jsonResponse(STATUS_BODY);
      if (url.includes("/import")) {
        return jsonResponse({ ...IMPORT_BODY, scopes: { "tbp-aaa": 12, "tbp-bbb": 8 } });
      }
      return undefined;
    });

    render(<TavernImportPanel companionId="companion-a" />);
    fireEvent.click(await screen.findByRole("button", { name: "同步新对话" }));

    expect(await screen.findByText(/写入 2 个陪伴对象的记忆/)).toBeTruthy();
  });

  it("只写进一个陪伴对象时不提多对象，避免噪声", async () => {
    stubRoutes((url) => {
      if (url.includes("/status")) return jsonResponse(STATUS_BODY);
      if (url.includes("/import")) return jsonResponse(IMPORT_BODY);
      return undefined;
    });

    render(<TavernImportPanel companionId="companion-a" />);
    fireEvent.click(await screen.findByRole("button", { name: "同步新对话" }));

    expect(await screen.findByText(/已导入 4 个会话（跳过 1）/)).toBeTruthy();
    expect(screen.queryByText(/个陪伴对象的记忆/)).toBeNull();
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

  it("没有可用的陪伴对象时不请求酒馆状态，并指路去创作工坊", async () => {
    const urls = stubRoutes((url) =>
      url.includes("/chat/personas")
        ? jsonResponse({ default_persona_id: "", personas: [] })
        : jsonResponse(STATUS_BODY),
    );

    render(<TavernImportPanel companionId="" />);

    expect(await screen.findByText(/还没有可用的陪伴对象/)).toBeTruthy();
    // 没有归属就不该去读酒馆数据：读了也没地方写
    expect(urls.some((line) => line.includes("/status"))).toBe(false);
  });

  it("只有一个陪伴对象时直接采用它，不再让用户去别处选", async () => {
    // 「没选中」常常只是宿主还没把清单读回来，不是真的要用户做选择
    const urls = stubRoutes((url) =>
      url.includes("/chat/personas")
        ? jsonResponse({
            default_persona_id: "only",
            personas: [{ id: "only", name: "唯一", title: "" }],
          })
        : jsonResponse(STATUS_BODY),
    );

    render(<TavernImportPanel companionId="" />);

    expect(await screen.findByText("D:/SillyTavern/data/default-user")).toBeTruthy();
    expect(urls.some((line) => line.includes("companion_id=only"))).toBe(true);
  });

  it("面板自带陪伴对象选择器：换一个就按新对象读状态并通知宿主", async () => {
    // 回归：控制台曾把数字人模型 id 当 companionId 传进来（值恒为空串），
    // 面板只剩一句「请先选择一个陪伴对象」而没有可选入口——面板必须自己能选
    const changed: string[] = [];
    const urls = stubRoutes((url) =>
      url.includes("/chat/personas")
        ? jsonResponse({
            default_persona_id: "a",
            personas: [
              { id: "a", name: "甲", title: "" },
              { id: "b", name: "乙", title: "" },
            ],
          })
        : jsonResponse(STATUS_BODY),
    );

    render(
      <TavernImportPanel companionId="a" onCompanionChange={(id) => changed.push(id)} />,
    );

    const select = await screen.findByLabelText("导入到");
    fireEvent.change(select, { target: { value: "b" } });

    await waitFor(() =>
      expect(urls.some((line) => line.includes("companion_id=b"))).toBe(true),
    );
    expect(changed).toEqual(["b"]);
  });

  it("勾选世界书不会重拉整个状态接口，只就地更新", async () => {
    // 需求原话：「不要取消一个世界书选择就重新加载一遍」。
    // status 是重接口（十秒上下），勾一下只是本地状态变了。
    const books = [
      { source: "world/甲", label: "世界书·甲", entries: 12, enabled: true },
      { source: "char/乙", label: "角色内嵌设定·乙", entries: 3, enabled: true },
    ];
    const urls: string[] = [];
    let saved: Record<string, unknown> | null = null;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        urls.push(`${init?.method ?? "GET"} ${url}`);
        if (url.includes("/settings") && init?.method === "PUT") {
          saved = (JSON.parse(String(init.body)) as { values: Record<string, unknown> }).values;
          return jsonResponse({ id: "tavern-bridge", values: saved, secrets_set: [] });
        }
        if (url.includes("/settings")) {
          return jsonResponse({
            id: "tavern-bridge",
            values: { tavern_dir: "D:/SillyTavern", tavern_disabled_books: [] },
            secrets_set: [],
          });
        }
        if (url.includes("/status")) {
          return jsonResponse({ ...STATUS_BODY, available: { entries: 15 }, books });
        }
        return jsonResponse({});
      }),
    );

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText(/参与接入的世界书/)).toBeTruthy();
    expect(screen.getByText("世界书条目 15")).toBeTruthy();
    const statusCallsBefore = urls.filter((line) => line.includes("/status")).length;

    fireEvent.click(screen.getByRole("checkbox", { name: /世界书·甲/ }));

    await waitFor(() => expect(saved).not.toBeNull());
    // 勾选立刻生效（受控勾选框不闪回）
    expect(
      (screen.getByRole("checkbox", { name: /世界书·甲/ }) as HTMLInputElement).checked,
    ).toBe(false);
    // 参与接入的条目数就地减掉这本书的 12 条
    expect(screen.getByText("世界书条目 3")).toBeTruthy();
    // 关键：没有再打一次状态接口
    expect(urls.filter((line) => line.includes("/status")).length).toBe(statusCallsBefore);
  });

  it("勾选保存失败时回滚界面状态", async () => {
    const books = [{ source: "world/甲", label: "世界书·甲", entries: 12, enabled: true }];

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/settings") && init?.method === "PUT") {
          return jsonResponse({ detail: "配置写不进去" }, 500);
        }
        if (url.includes("/settings")) {
          return jsonResponse({
            id: "tavern-bridge",
            values: { tavern_dir: "D:/SillyTavern", tavern_disabled_books: [] },
            secrets_set: [],
          });
        }
        if (url.includes("/status")) return jsonResponse({ ...STATUS_BODY, books });
        return jsonResponse({});
      }),
    );

    render(<TavernImportPanel companionId="companion-a" />);

    fireEvent.click(await screen.findByRole("checkbox", { name: /世界书·甲/ }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    // 后端一字未改（PUT 是全量写），界面必须退回改动前，不能留下假象
    expect(
      (screen.getByRole("checkbox", { name: /世界书·甲/ }) as HTMLInputElement).checked,
    ).toBe(true);
  });

  it("世界书全部取消勾选后勾选区仍在，而且还能勾回来", async () => {
    // 回归：曾经用 `available.entries > 0` 当渲染条件，而它是**筛选后**的计数——
    // 把书全取消勾选后条目数归零，整个勾选区连同每一本书一起消失，用户再也勾不回来。
    // 后端 `book_catalog()` 特意不受勾选影响就是为了防这个，前端不能把它绕回去。
    const books = [
      { source: "world/甲", label: "世界书·甲", entries: 12, enabled: true },
      { source: "char/乙", label: "角色内嵌设定·乙", entries: 3, enabled: true },
    ];
    let disabledBooks: string[] = [];

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/settings") && init?.method === "PUT") {
          disabledBooks = (
            JSON.parse(String(init.body)) as { values: { tavern_disabled_books: string[] } }
          ).values.tavern_disabled_books;
          return jsonResponse({ id: "tavern-bridge", values: {}, secrets_set: [] });
        }
        if (url.includes("/settings")) {
          return jsonResponse({
            id: "tavern-bridge",
            values: { tavern_dir: "D:/SillyTavern", tavern_disabled_books: disabledBooks },
            secrets_set: [],
          });
        }
        if (url.includes("/status")) {
          // books 是**完整清单**（后端不受勾选影响）；available 是筛选后的计数
          return jsonResponse({ ...STATUS_BODY, available: { entries: 15 }, books });
        }
        return jsonResponse({});
      }),
    );

    /** 取消一本：保存期间整组勾选框会禁用，所以先等它恢复可点 */
    async function uncheck(name: RegExp) {
      await waitFor(() =>
        expect((screen.getByRole("checkbox", { name }) as HTMLInputElement).disabled).toBe(false),
      );
      fireEvent.click(screen.getByRole("checkbox", { name }));
      await waitFor(() =>
        expect((screen.getByRole("checkbox", { name }) as HTMLInputElement).checked).toBe(false),
      );
    }

    render(<TavernImportPanel companionId="companion-a" />);

    expect(await screen.findByText(/参与接入的世界书/)).toBeTruthy();
    expect(screen.getByText("世界书条目 15")).toBeTruthy();

    await uncheck(/世界书·甲/);
    expect(screen.getByText("世界书条目 3")).toBeTruthy();

    await uncheck(/角色内嵌设定·乙/);

    // 条目数已归零，但勾选区必须还在——否则用户没有任何办法勾回来
    expect(screen.getByText("世界书条目 0")).toBeTruthy();
    expect(screen.getByText(/参与接入的世界书/)).toBeTruthy();
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    // 两份禁用清单都要落库（12 条那本 + 3 条那本）
    expect([...disabledBooks].sort()).toEqual(["char/乙", "world/甲"]);

    // 勾回来：条目数就地加回去
    fireEvent.click(screen.getByRole("checkbox", { name: /世界书·甲/ }));
    await waitFor(() => expect(screen.getByText("世界书条目 12")).toBeTruthy());
  });
});
