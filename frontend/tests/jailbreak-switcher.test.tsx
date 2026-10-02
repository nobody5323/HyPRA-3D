/**
 * 叙事框架（jailbreak）切换器测试。
 *
 * 这个控件的核心不是「能不能选」，而是**「未设置」在界面上要显示成什么**：
 * 后端偏好里的空串意思是「跟随部署默认」，而部署默认可能是开启的。若把空串
 * 一律渲染成「不使用」，用户看到的就是一句假话——以为自己没开，实际每轮都在开。
 *
 * 另一半是成人向档位的确认：后端把「谁算成年」的判断交给前端
 * （`requires_adult` 只做声明、不拦截），所以那道确认是真实职责，测试要守住它
 * 不能退化成一次点击就生效。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JailbreakSwitcher } from "@/components/settings/JailbreakSwitcher";
import type { JailbreakCatalog } from "@/lib/api/types";

const CATALOG: JailbreakCatalog = {
  enabled: false,
  default_jailbreak_id: "immersive-narrative",
  presets: [
    {
      id: "immersive-narrative",
      name: "沉浸叙事",
      description: "把对话放进虚构叙事框架",
      tags: ["通用"],
      intensity: 1,
      intensity_label: "中度：声明虚构语境 + 抑制跳出角色的回应",
      requires_adult: false,
    },
    {
      id: "mature-fiction",
      name: "成人向虚构",
      description: "在虚构语境内放开题材",
      tags: ["成人向"],
      intensity: 2,
      intensity_label: "强度：在虚构语境内放开题材，并明确边界",
      requires_adult: true,
    },
  ],
};

afterEach(cleanup);

describe("JailbreakSwitcher", () => {
  it("选项里包含「不使用」与后端清单里的各档", () => {
    // 触发器上也会显示当前档名，所以这里给一个具体档，让「不使用」只出现在列表里
    render(
      <JailbreakSwitcher
        value="immersive-narrative"
        onChange={() => {}}
        catalog={CATALOG}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /叙事框架/ }));

    expect(screen.getByText("不使用")).toBeTruthy();
    expect(screen.getAllByText("沉浸叙事").length).toBeGreaterThan(0);
    expect(screen.getAllByText("成人向虚构").length).toBeGreaterThan(0);
  });

  it("选中某一档时把它的 id 传出去", () => {
    const onChange = vi.fn();
    render(<JailbreakSwitcher value="none" onChange={onChange} catalog={CATALOG} />);

    fireEvent.click(screen.getByRole("button", { name: /叙事框架/ }));
    fireEvent.click(screen.getByText("沉浸叙事"));

    expect(onChange).toHaveBeenCalledWith("immersive-narrative");
  });

  it("★ 未设置（空串）而部署默认开启时，触发器显示的是那一档，不是「不使用」", () => {
    render(
      <JailbreakSwitcher
        value=""
        onChange={() => {}}
        catalog={{ ...CATALOG, enabled: true }}
      />,
    );

    // 显示「不使用」会在部署默认开启时对用户说谎
    expect(screen.getByRole("button", { name: /沉浸叙事/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /不使用/ })).toBeNull();
  });

  it("未设置且部署默认关闭时，触发器显示「不使用」", () => {
    render(<JailbreakSwitcher value="" onChange={() => {}} catalog={CATALOG} />);

    expect(screen.getByRole("button", { name: /不使用/ })).toBeTruthy();
  });

  it("用户显式关掉（none）时显示「不使用」，即使部署默认是开启的", () => {
    render(
      <JailbreakSwitcher
        value="none"
        onChange={() => {}}
        catalog={{ ...CATALOG, enabled: true }}
      />,
    );

    expect(screen.getByRole("button", { name: /不使用/ })).toBeTruthy();
  });

  it("★ 成人向档位需要一次确认，点一下不会直接生效", () => {
    const onChange = vi.fn();
    render(<JailbreakSwitcher value="none" onChange={onChange} catalog={CATALOG} />);

    fireEvent.click(screen.getByRole("button", { name: /叙事框架/ }));
    fireEvent.click(screen.getByText("成人向虚构"));

    // 第一次点击只弹出确认，不能已经生效
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/仅限已满 18 岁的用户/)).toBeTruthy();

    fireEvent.click(screen.getByText("我已满 18 岁，开启"));

    expect(onChange).toHaveBeenCalledWith("mature-fiction");
  });

  it("成人向档位的确认可以取消（取消后不生效）", () => {
    const onChange = vi.fn();
    render(<JailbreakSwitcher value="none" onChange={onChange} catalog={CATALOG} />);

    fireEvent.click(screen.getByRole("button", { name: /叙事框架/ }));
    fireEvent.click(screen.getByText("成人向虚构"));
    fireEvent.click(screen.getByText("取消"));

    expect(onChange).not.toHaveBeenCalled();
  });

  it("catalog 为 null 时不渲染（后端读不到，留着只会点了没反应）", () => {
    const { container } = render(
      <JailbreakSwitcher value="" onChange={() => {}} catalog={null} />,
    );

    expect(container.querySelector("button")).toBeNull();
  });
});
