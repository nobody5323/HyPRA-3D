/**
 * 能力中心面板测试：分层渲染、状态展示、只读标记、失败原因、空数据不渲染。
 *
 * 纯展示组件，不发请求——数据由页面从 `/health` 取回后传入。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PluginCenter } from "@/components/settings/PluginCenter";
import type { PluginStatus, PluginsSummary } from "@/lib/api/types";

afterEach(cleanup);

function makePlugin(overrides: Partial<PluginStatus> = {}): PluginStatus {
  return {
    id: "llm-providers",
    display_name: "对话模型接入",
    version: "0.1.0",
    layer: "builtin",
    category: "provider",
    state: "started",
    capabilities: ["provider"],
    read_only: true,
    error: "",
    ...overrides,
  };
}

function makeSummary(overrides: Partial<PluginsSummary> = {}): PluginsSummary {
  return { total: 1, started: 1, failed: 0, by_layer: {}, ...overrides };
}

describe("PluginCenter", () => {
  it("后端未就绪（无插件数据）时不渲染", () => {
    const { container } = render(<PluginCenter plugins={[]} summary={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("显示标题与运行统计", () => {
    render(
      <PluginCenter
        plugins={[makePlugin()]}
        summary={makeSummary({ total: 7, started: 6, failed: 1 })}
      />,
    );

    expect(screen.getByText("能力中心")).toBeTruthy();
    expect(screen.getByText(/6\/7 运行中/)).toBeTruthy();
    expect(screen.getByText(/1 失败/)).toBeTruthy();
  });

  it("按 core / builtin / third-party 分层分组，且只渲染存在的层", () => {
    render(
      <PluginCenter
        plugins={[
          makePlugin({ id: "c1", display_name: "核心甲", layer: "core", read_only: false }),
          makePlugin({ id: "b1", display_name: "内置乙" }),
          makePlugin({ id: "b2", display_name: "内置丙" }),
        ]}
        summary={makeSummary()}
      />,
    );

    expect(screen.getByText("核心（1）")).toBeTruthy();
    expect(screen.getByText("内置（2）")).toBeTruthy();
    expect(screen.queryByText(/第三方/)).toBeNull(); // 无第三方插件 → 整层不渲染
    expect(screen.getByText("核心甲")).toBeTruthy();
    expect(screen.getByText("内置丙")).toBeTruthy();
  });

  it("运行中/已禁用/失败 三种状态各有对应文案", () => {
    render(
      <PluginCenter
        plugins={[
          makePlugin({ id: "a", display_name: "甲", state: "started" }),
          makePlugin({ id: "b", display_name: "乙", state: "disabled" }),
          makePlugin({ id: "c", display_name: "丙", state: "failed" }),
        ]}
        summary={makeSummary()}
      />,
    );

    expect(screen.getByText("运行中")).toBeTruthy();
    expect(screen.getByText("已禁用")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
  });

  it("只读插件打上「只读」标记，可写插件不打", () => {
    render(
      <PluginCenter
        plugins={[
          makePlugin({ id: "ro", display_name: "只读插件", read_only: true }),
          makePlugin({ id: "rw", display_name: "可写插件", read_only: false }),
        ]}
        summary={makeSummary()}
      />,
    );

    // 只读标记只出现一次（可写插件不渲染该标记）
    expect(screen.getAllByText("只读")).toHaveLength(1);
    expect(screen.getByTitle(/宿主未授予写句柄/)).toBeTruthy();
  });

  it("失败插件显示错误原因", () => {
    render(
      <PluginCenter
        plugins={[
          makePlugin({
            id: "bad",
            display_name: "坏插件",
            state: "failed",
            error: "入口文件不存在：plugin.py",
          }),
        ]}
        summary={makeSummary({ failed: 1 })}
      />,
    );

    expect(screen.getByText("入口文件不存在：plugin.py")).toBeTruthy();
  });

  it("展示插件贡献的能力面", () => {
    render(
      <PluginCenter
        plugins={[makePlugin({ capabilities: ["datasource", "tool", "settings"] })]}
        summary={makeSummary()}
      />,
    );

    expect(screen.getByText("datasource · tool · settings")).toBeTruthy();
  });

  it("无 summary 时只渲染列表，不报错", () => {
    render(<PluginCenter plugins={[makePlugin()]} summary={null} />);
    expect(screen.getByText("对话模型接入")).toBeTruthy();
    expect(screen.queryByText(/运行中\b.*\//)).toBeNull();
  });
});
