/**
 * 模型来源面板测试（`GET /media/avatar/models/sources` 的界面层）。
 *
 * 重点在**合规信息的呈现**：
 * 1. 模型不随本项目分发，因此这里只能有指向来源页的链接，**不能有「安装」按钮**；
 * 2. 授权未标注时必须明确写出来——留空会让人以为没有条款限制；
 * 3. 已装的模型不在这里重复列出（那是「模型库」的职责）。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModelSourcePanel } from "@/components/avatar/ModelSourcePanel";
import type { AvatarModelDescriptor, AvatarModelSources } from "@/lib/api/types";

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

function stubSources(body: AvatarModelSources | unknown, status = 200): string[] {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      urls.push(String(input));
      return jsonResponse(body, status);
    }),
  );
  return urls;
}

function makeModel(overrides: Partial<AvatarModelDescriptor> = {}): AvatarModelDescriptor {
  return {
    id: "hiyori",
    displayName: "Hiyori",
    kind: "live2d",
    source: "remote-manifest",
    installed: false,
    entry: "",
    description: "",
    previewUrl: "",
    author: "",
    license: "",
    homepage: "",
    extra: {},
    ...overrides,
  };
}

function makeCatalog(overrides: Partial<AvatarModelSources> = {}): AvatarModelSources {
  return {
    sources: [
      { id: "local-library", available: true, description: "本机模型库", count: 0 },
      { id: "remote-manifest", available: true, description: "模型清单", count: 1 },
    ],
    models: [],
    installedCount: 0,
    availableCount: 0,
    ...overrides,
  };
}

describe("ModelSourcePanel 未配置清单", () => {
  it("清单没配时给出指路文案（并说明去哪配）", async () => {
    stubSources(
      makeCatalog({
        sources: [
          { id: "local-library", available: true, description: "本机模型库", count: 0 },
          { id: "remote-manifest", available: false, description: "未配置模型清单", count: 0 },
        ],
      }),
    );

    render(<ModelSourcePanel />);

    expect(await screen.findByText(/还没配置模型清单/)).toBeTruthy();
    expect(screen.getByText("model-manifest.example.json")).toBeTruthy();
    expect(screen.getByText(/数字人模型来源/)).toBeTruthy();
  });
});

describe("ModelSourcePanel 可获取列表", () => {
  it("列出可获取模型，含作者 / 授权 / 来源链接", async () => {
    stubSources(
      makeCatalog({
        models: [
          makeModel({
            displayName: "Hiyori",
            description: "示例模型",
            author: "某作者",
            license: "CC BY 4.0",
            homepage: "https://example.invalid/hiyori",
          }),
        ],
        availableCount: 1,
      }),
    );

    render(<ModelSourcePanel />);

    expect(await screen.findByText("Hiyori")).toBeTruthy();
    expect(screen.getByText("示例模型")).toBeTruthy();
    expect(screen.getByText(/作者：某作者/)).toBeTruthy();
    expect(screen.getByText(/授权：CC BY 4\.0/)).toBeTruthy();

    const link = screen.getByRole("link", { name: /到来源页获取/ });
    expect(link.getAttribute("href")).toBe("https://example.invalid/hiyori");
    expect(link.getAttribute("target")).toBe("_blank");
    // 外链必须带 noopener，否则新页面能通过 window.opener 反向操作本页
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("授权未标注时明确说出来（留空会让人以为没有条款限制）", async () => {
    stubSources(makeCatalog({ models: [makeModel({ license: "" })], availableCount: 1 }));

    render(<ModelSourcePanel />);

    expect(await screen.findByText(/授权：未标注/)).toBeTruthy();
  });

  it("已安装的模型不在「可获取」里重复列出", async () => {
    stubSources(
      makeCatalog({
        models: [
          makeModel({ id: "mine", displayName: "我的模型", installed: true }),
          makeModel({ id: "other", displayName: "别的模型", installed: false }),
        ],
        installedCount: 1,
        availableCount: 1,
      }),
    );

    render(<ModelSourcePanel />);

    expect(await screen.findByText("别的模型")).toBeTruthy();
    expect(screen.queryByText("我的模型")).toBeNull();
  });

  it("没有可获取项时说明原因", async () => {
    stubSources(makeCatalog({ models: [makeModel({ installed: true })] }));

    render(<ModelSourcePanel />);

    expect(await screen.findByText(/清单里没有可获取的模型/)).toBeTruthy();
  });

  it("清单没给获取地址时如实提示，而不是留个空链接", async () => {
    stubSources(makeCatalog({ models: [makeModel()], availableCount: 1 }));

    render(<ModelSourcePanel />);

    expect(await screen.findByText(/清单未给出获取地址/)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("始终提示模型不随本项目分发（合规）", async () => {
    stubSources(makeCatalog({ models: [makeModel()] }));

    render(<ModelSourcePanel />);

    expect(await screen.findByText(/模型不随本项目分发/)).toBeTruthy();
    // 也不该出现任何「安装 / 下载」按钮
    expect(screen.queryByRole("button", { name: /安装|下载/ })).toBeNull();
  });
});

describe("ModelSourcePanel 故障", () => {
  it("接口失败时给出可读错误", async () => {
    stubSources({ detail: "服务不可用" }, 500);

    render(<ModelSourcePanel />);

    expect((await screen.findByRole("alert")).textContent).toContain("服务不可用");
  });
});
