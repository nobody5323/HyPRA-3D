import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * 桌宠窗右键菜单里的「交互模式」切换（接线回归用例）。
 *
 * 与 `pet-session-panel.test.ts` 同一做法：desktop 的 vitest 跑在 node 环境、
 * 没有 DOM 基建（见 vitest.config.mts），所以断言**源码接线**而不是渲染结果。
 *
 * 守住的教训：桌宠窗是长期开着的那个窗口，而「交互模式」此前只能靠选没选酒馆预设
 * 隐式决定，界面上没有任何入口。后果实测过——用户在桌宠模式下问酒馆设定，酒馆
 * 世界书按设计不参与召回，他找不到任何线索，只能得出「召回坏了」的结论。
 */

const PET_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "../src/renderer/pet");

const app = fs.readFileSync(path.join(PET_DIR, "App.tsx"), "utf8");
const menu = fs.readFileSync(path.join(PET_DIR, "ContextMenu.tsx"), "utf8");
const css = fs.readFileSync(path.join(PET_DIR, "pet.css"), "utf8");

describe("桌宠右键菜单的交互模式", () => {
  it("两个模式都在菜单里，并且真的接到 session.setMode 上（不是空壳项）", () => {
    expect(app).toContain("session.setMode(MODE_COMPANION)");
    expect(app).toContain("session.setMode(MODE_TAVERN)");
    expect(app).toContain("MODE_COMPANION");
    expect(app).toContain("MODE_TAVERN");
  });

  it("★ 勾选态用「选定值，没选过则用后端实际生效值」", () => {
    // 只用 session.mode 的话，没选过时两项都不勾，而对话明明按酒馆模式在跑
    expect(app).toContain("session.mode || session.effectiveMode");
  });

  it("★ 互斥的一组用 menuitemradio（不是 checkbox）", () => {
    // 两个 checkbox 会让读屏念出「两个都勾上」这种不可能的状态
    expect(app).toContain('role: "menuitemradio"');
    expect(menu).toContain('item.role ?? "menuitemcheckbox"');
  });

  it("每项带一句后果说明（光看名字不知道会不会召回酒馆世界书）", () => {
    expect(app).toContain("酒馆世界书不参与召回");
    expect(app).toContain("酒馆世界书参与召回");
    expect(menu).toContain("item.hint");
    expect(css).toContain(".petMenuHint");
  });

  it("★ 菜单落位按实测尺寸算，不再用写死的估算（旧写法会把最后一项裁掉）", () => {
    // 菜单高度取决于 hint 行数 / 字号 / 分隔线 / --pet-scale，估算必然漂移：
    // 旧实现拿 188×26 常量夹取，105% 缩放下就短了约 30px，贴底右键会裁掉「退出 HyPRA」
    expect(menu).toContain("getBoundingClientRect");
    expect(menu).toContain("placeContextMenu");
    // 落位规则本身在 pet-menu.ts（纯函数，见 pet-menu.test.ts）
    expect(menu).not.toContain("HINT_HEIGHT");
  });
});
