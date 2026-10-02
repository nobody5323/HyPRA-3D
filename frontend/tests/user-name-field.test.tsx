/**
 * 「用户称呼」输入框测试。
 *
 * 重点（都是这类「输入 + 保存」控件的真实故障模式）：
 * 1. 保存前**规范化**（去首尾空白）——不然「阿岸 」会被原样写进提示词；
 * 2. 成功/失败都要有可读反馈——静默保存会让用户以为没生效，反复点；
 * 3. 没改动时按钮不可用——避免无意义写盘；
 * 4. 留空保存是**合法操作**（清除），不能被当成「什么都没填」拦下。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { UserNameField } from "@/components/settings/UserNameField";

const LABEL = "你希望角色怎么称呼你";

afterEach(() => {
  cleanup();
});

function button(): HTMLButtonElement {
  return screen.getByRole("button", { name: /保存|保存中/ }) as HTMLButtonElement;
}

describe("UserNameField", () => {
  it("保存时把去空白后的称呼交给 onSave", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<UserNameField idPrefix="t" value="朋友" onSave={onSave} />);

    fireEvent.change(screen.getByLabelText(LABEL), { target: { value: "  阿岸  " } });
    fireEvent.click(button());

    await waitFor(() => expect(onSave).toHaveBeenCalledWith("阿岸"));
  });

  it("保存成功给出可读反馈（用户知道存上了）", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<UserNameField idPrefix="t" value="朋友" onSave={onSave} />);

    fireEvent.change(screen.getByLabelText(LABEL), { target: { value: "阿岸" } });
    fireEvent.click(button());

    expect((await screen.findByRole("status")).textContent).toContain("阿岸");
  });

  it("保存失败展示后端给的原因，而不是一句「已保存」", async () => {
    const onSave = vi.fn().mockRejectedValue(new Error("user_name 过长（上限 24 字符）"));
    render(<UserNameField idPrefix="t" value="朋友" onSave={onSave} />);

    fireEvent.change(screen.getByLabelText(LABEL), { target: { value: "阿岸" } });
    fireEvent.click(button());

    expect((await screen.findByRole("alert")).textContent).toContain("过长");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("输入没变时保存按钮不可用（不做无意义写盘）", () => {
    render(<UserNameField idPrefix="t" value="阿岸" onSave={vi.fn()} />);

    expect(button().disabled).toBe(true);
  });

  it("留空保存是合法的清除操作", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<UserNameField idPrefix="t" value="阿岸" onSave={onSave} />);

    fireEvent.change(screen.getByLabelText(LABEL), { target: { value: "" } });
    fireEvent.click(button());

    await waitFor(() => expect(onSave).toHaveBeenCalledWith(""));
  });

  it("输入框有长度上限（与后端同一口径，超长在本地就写不进去）", () => {
    render(<UserNameField idPrefix="t" value="朋友" onSave={vi.fn()} />);

    expect((screen.getByLabelText(LABEL) as HTMLInputElement).maxLength).toBe(24);
  });
});
