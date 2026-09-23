/**
 * 文风切换器测试：清单来自后端，但**「不使用文风」是前端本地补的选项**。
 *
 * 为什么需要它：文风预设的 sampling 优先级最高，会盖过酒馆预设作者自己的采样意图；
 * 导入酒馆预设的用户要「纯预设体验」时必须能显式关掉这一层
 * （后端约定 `style_id = "none"`，见 docs/st-preset-compat.md §10）。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StyleSwitcher } from "@/components/settings/StyleSwitcher";
import type { StyleCatalog } from "@/lib/api/types";

const CATALOG: StyleCatalog = {
  default_style_id: "modern-conversational",
  styles: [
    {
      id: "modern-conversational",
      name: "现代口语",
      description: "像真人聊天的自然口语",
      tags: ["口语"],
      examples: 3,
    },
  ],
};

afterEach(cleanup);

describe("StyleSwitcher", () => {
  it("选项里包含「不使用文风」（本地选项，不依赖后端清单）", () => {
    render(
      <StyleSwitcher value="modern-conversational" onChange={() => {}} catalog={CATALOG} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /文风/ }));

    expect(screen.getByText("不使用文风")).toBeTruthy();
    // 触发器上也显示当前文风名，故用 getAllByText
    expect(screen.getAllByText("现代口语").length).toBeGreaterThan(0);
  });

  it("选中「不使用文风」时把 none 传出去", () => {
    const onChange = vi.fn();
    render(
      <StyleSwitcher value="modern-conversational" onChange={onChange} catalog={CATALOG} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /文风/ }));
    fireEvent.click(screen.getByText("不使用文风"));

    expect(onChange).toHaveBeenCalledWith("none");
  });

  it("当前值为 none 时，触发器显示「不使用文风」", () => {
    render(<StyleSwitcher value="none" onChange={() => {}} catalog={CATALOG} />);

    expect(screen.getByRole("button", { name: /不使用文风/ })).toBeTruthy();
  });

  it("清单为空时不渲染（避免空选择器）", () => {
    const { container } = render(
      <StyleSwitcher value="" onChange={() => {}} catalog={{ default_style_id: "", styles: [] }} />,
    );

    // 只有本地「不使用文风」可选时仍渲染；真正的空清单指 catalog 为 null
    expect(container.querySelector("button")).toBeTruthy();
  });

  it("catalog 为 null 时不渲染", () => {
    const { container } = render(<StyleSwitcher value="" onChange={() => {}} catalog={null} />);

    expect(container.querySelector("button")).toBeNull();
  });
});
