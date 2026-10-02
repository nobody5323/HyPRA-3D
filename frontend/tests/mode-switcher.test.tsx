/**
 * 交互模式切换器测试。
 *
 * 重点：
 * 1. **默认展开**——这是个会改变召回行为的开关，藏起来等于没有（需求原话：
 *    「做的清晰一点，不要默认隐藏」）；
 * 2. **点当前模式不触发切换**——切换会开新对话，为一次没有变化的点击清掉对话
 *    是不可接受的（真正的守卫在 `useChatSession.changeMode`，这里守界面不误报）；
 * 3. 未手动选择时要说清「后端自动判断成了哪个」，否则用户看到「一个都没选」而
 *    对话明明按酒馆模式在跑。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModeSwitcher } from "@/components/settings/ModeSwitcher";
import { MODE_COMPANION, MODE_TAVERN } from "@/lib/chat/mode";

afterEach(cleanup);

function renderSwitcher(props: Partial<Parameters<typeof ModeSwitcher>[0]> = {}) {
  const onChange = vi.fn();
  render(
    <ModeSwitcher
      value={MODE_COMPANION}
      effective={MODE_COMPANION}
      onChange={onChange}
      {...props}
    />,
  );
  return { onChange };
}

describe("ModeSwitcher", () => {
  it("★ 默认展开：两个选项与说明都直接可见", () => {
    renderSwitcher();

    expect(screen.getByRole("radio", { name: /桌宠对话/ })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /酒馆聊天/ })).toBeTruthy();
    // 说明必须直接写出来，用户不该先理解记忆作用域才能做对选择
    expect(screen.getByText(/酒馆世界书不参与召回/)).toBeTruthy();
    expect(screen.getByText(/酒馆世界书参与召回/)).toBeTruthy();
  });

  it("可以收起，也能再展开（收起是留给已调好的人）", () => {
    renderSwitcher();

    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    expect(screen.queryByRole("radio")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "展开" }));
    expect(screen.getByRole("radio", { name: /酒馆聊天/ })).toBeTruthy();
  });

  it("标出当前选中的模式", () => {
    renderSwitcher({ value: MODE_TAVERN, effective: MODE_TAVERN });

    expect(screen.getByRole("radio", { name: /酒馆聊天/ }).getAttribute("aria-checked")).toBe(
      "true",
    );
    expect(screen.getByRole("radio", { name: /桌宠对话/ }).getAttribute("aria-checked")).toBe(
      "false",
    );
  });

  it("切到另一个模式会回调它的 id", () => {
    const { onChange } = renderSwitcher({ value: MODE_COMPANION });

    fireEvent.click(screen.getByRole("radio", { name: /酒馆聊天/ }));

    expect(onChange).toHaveBeenCalledWith(MODE_TAVERN);
  });

  it("★ 点当前模式不回调（切换会开新对话，不该被空点击触发）", () => {
    const { onChange } = renderSwitcher({ value: MODE_COMPANION });

    fireEvent.click(screen.getByRole("radio", { name: /桌宠对话/ }));

    expect(onChange).not.toHaveBeenCalled();
  });

  it("★ 没手动选过时说明后端自动判断的结果", () => {
    renderSwitcher({ value: "", effective: MODE_TAVERN });

    expect(screen.getByText("自动：酒馆聊天")).toBeTruthy();
    expect(screen.getByText(/本轮实际生效/)).toBeTruthy();
  });

  it("没选过也没聊过时不编造模式名", () => {
    renderSwitcher({ value: "", effective: "" });

    expect(screen.getByText("未选择")).toBeTruthy();
  });

  it("对话进行中禁用两个选项", () => {
    const { onChange } = renderSwitcher({ disabled: true });

    const tavern = screen.getByRole("radio", { name: /酒馆聊天/ }) as HTMLButtonElement;
    expect(tavern.disabled).toBe(true);
    fireEvent.click(tavern);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("写明切换会开始新对话（否则会被当成丢数据的 bug）", () => {
    renderSwitcher();

    expect(screen.getByText(/开始新对话/)).toBeTruthy();
    expect(screen.getByText(/历史记录/)).toBeTruthy();
  });

  it("★ 写明模式与酒馆预设正交（桌宠模式也能用酒馆预设）", () => {
    // 这是用户明确的口径（「桌宠模式也可以用酒馆预设」）：模式只管召回范围，
    // 提示词走哪条路由「酒馆预设」单独选。不写出来，用户会以为两者互斥。
    renderSwitcher();

    expect(screen.getByText(/两种模式都可以用酒馆预设/)).toBeTruthy();
    // 也要点明去哪个栏选（文案里提到两次「酒馆预设」，用 getAllByText）
    expect(screen.getAllByText(/酒馆预设/).length).toBeGreaterThan(0);
  });
});
