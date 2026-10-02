/**
 * 「它还能这样用」的一次性提示。
 *
 * 这条提示解决的是一个具体的疑虑：语音与发图都是**入口可见、能力不可见**的功能
 * ——按钮摆在那里，但用户不知道「点了会怎样」，尤其不知道
 * **识别结果不会自动发送**，于是不敢点。所以文案里必须写出这一点。
 *
 * 同时守住两条：没有能力时不提示（提示了也点不到按钮，只会更困惑）、
 * 关掉后记住（它是上手引导，不是常驻说明）。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CapabilityHint, capabilityHintText } from "@/components/chat/CapabilityHint";
import { dismissHint, isHintDismissed } from "@/lib/chat/session-store";

const HINT_ID = "test-hint";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("capabilityHintText", () => {
  it("两种能力都有时一并说明，并带上快捷键", () => {
    const text = capabilityHintText(true, true, "Ctrl+Shift+M");
    expect(text).toContain("图片");
    expect(text).toContain("语音");
    expect(text).toContain("Ctrl+Shift+M");
    expect(text).toContain("不会自动发送");
  });

  it("没配快捷键时不出现「按」字样", () => {
    expect(capabilityHintText(true, true, "")).not.toContain("或按");
  });

  it("只有语音时只提语音", () => {
    const text = capabilityHintText(true, false, "");
    expect(text).toContain("语音");
    expect(text).not.toContain("图片");
  });

  it("只有图片时只提图片，并说明图片不落盘", () => {
    const text = capabilityHintText(false, true, "");
    expect(text).toContain("图片");
    expect(text).not.toContain("语音");
    expect(text).toContain("不会存到后端");
  });

  it("都没有时返回空串（调用方据此不渲染）", () => {
    expect(capabilityHintText(false, false, "")).toBe("");
  });
});

describe("CapabilityHint", () => {
  it("有能力时显示提示", () => {
    render(
      <CapabilityHint showMic showImage shortcutLabel="" hintId={HINT_ID} />,
    );
    expect(screen.getByText(/它还能「看」和「听」/)).toBeTruthy();
  });

  it("没有能力时**不渲染**（提示了也点不到按钮，只会更困惑）", () => {
    const { container } = render(
      <CapabilityHint showMic={false} showImage={false} shortcutLabel="" hintId={HINT_ID} />,
    );
    expect(container.textContent).toBe("");
  });

  it("点「知道了」后记住不再显示", async () => {
    const { rerender } = render(
      <CapabilityHint showMic showImage shortcutLabel="" hintId={HINT_ID} />,
    );

    screen.getByRole("button", { name: "知道了" }).click();
    expect(isHintDismissed(HINT_ID)).toBe(true);

    // 重新挂载（模拟刷新页面）后仍不显示
    rerender(
      <CapabilityHint showMic showImage shortcutLabel="" hintId={HINT_ID} key="remount" />,
    );
    expect(screen.queryByText(/它还能「看」和「听」/)).toBeNull();
  });

  it("已关掉的提示在挂载时不显示", () => {
    dismissHint(HINT_ID);
    const { container } = render(
      <CapabilityHint showMic showImage shortcutLabel="" hintId={HINT_ID} />,
    );
    expect(container.textContent).toBe("");
  });

  it("不同 hintId 互不影响（关掉 A 不该把 B 也关掉）", () => {
    dismissHint("hint-a");
    expect(isHintDismissed("hint-a")).toBe(true);
    expect(isHintDismissed("hint-b")).toBe(false);
  });
});
