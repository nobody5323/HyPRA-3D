/**
 * 「本轮记忆与行动」面板测试。
 *
 * 重点只有一个：**模式要露出来**。酒馆世界书只在酒馆聊天模式下召回
 * （`backend/app/memory/knowledge/scopes.py`），用户在桌宠模式下问酒馆设定却
 * 什么都召不回时，面板上的「模式」是唯一能解释这件事的东西——此前它不在界面上，
 * 只能翻后端留痕，实测被读成「召回坏了」。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryTrace } from "@/components/chat/MemoryTrace";
import { MODE_COMPANION, MODE_TAVERN, modeLabel } from "@/lib/chat/mode";

afterEach(cleanup);

function renderTrace(props: Partial<Parameters<typeof MemoryTrace>[0]> = {}) {
  return render(
    <MemoryTrace
      mode={MODE_COMPANION}
      memoryCounts={{}}
      knowledgeHits={0}
      worldbookHits={[]}
      memoryScheduled={false}
      estimatedTokens={0}
      warnings={[]}
      {...props}
    />,
  );
}

describe("modeLabel", () => {
  it("认识两种模式", () => {
    expect(modeLabel(MODE_COMPANION)).toBe("桌宠对话");
    expect(modeLabel(MODE_TAVERN)).toBe("酒馆聊天");
  });

  it("未知值返回空串：宁可不显示，也不要显示一个错的名字", () => {
    expect(modeLabel("")).toBe("");
    expect(modeLabel(undefined)).toBe("");
    expect(modeLabel("something-else")).toBe("");
  });
});

describe("MemoryTrace", () => {
  it("桌宠模式且什么都没召回时不渲染（默认档不该有常驻噪声）", () => {
    const { container } = renderTrace({ mode: MODE_COMPANION });

    expect(container.innerHTML).toBe("");
  });

  it("★ 酒馆模式即使什么都没召回也要渲染，并写明模式", () => {
    renderTrace({ mode: MODE_TAVERN });

    expect(screen.getByText("酒馆聊天")).toBeTruthy();
  });

  it("有召回时两种模式都显示模式名", () => {
    renderTrace({ mode: MODE_COMPANION, knowledgeHits: 3 });

    expect(screen.getByText("桌宠对话")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
  });

  it("模式未知（老后端）时不显示模式，其余照旧", () => {
    renderTrace({ mode: "", knowledgeHits: 1 });

    expect(screen.queryByText(/模式：/)).toBeNull();
    expect(screen.getByText("1")).toBeTruthy();
  });
});
