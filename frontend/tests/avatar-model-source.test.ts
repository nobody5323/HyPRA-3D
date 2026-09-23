/**
 * 「选中的模型 → 渲染器输入」的翻译测试。
 *
 * 这层最容易出的错是**两种"没有 URL"被混为一谈**：
 * 「用内置模型」与「选了静态立绘模型」都会让 `live2dUrl` 为空，
 * 但行为完全相反（一个要走 Live2D，一个绝不能走）。所以这里用可辨识联合，
 * 并把两种情况都钉死。
 */

import { describe, expect, it } from "vitest";

import { BUILT_IN_MODEL_SOURCE, resolveAvatarModelSource } from "@/lib/avatar/avatar-model-source";
import type { AvatarModelInfo } from "@/lib/api/types";

function makeModel(overrides: Partial<AvatarModelInfo>): AvatarModelInfo {
  return {
    id: "m_test",
    name: "测试模型",
    kind: "live2d",
    createdAt: "",
    updatedAt: "",
    entry: "pet.model3.json",
    expressions: [],
    images: [],
    expressionMap: {},
    layout: null,
    meta: {},
    ...overrides,
  };
}

describe("resolveAvatarModelSource", () => {
  it("没有选中模型 → 内置（界面不选任何模型也能演示）", () => {
    expect(resolveAvatarModelSource(null)).toEqual(BUILT_IN_MODEL_SOURCE);
  });

  it("Live2D 模型 → 给出入口 URL", () => {
    const source = resolveAvatarModelSource(makeModel({ id: "m_a" }));
    expect(source.kind).toBe("live2d");
    if (source.kind !== "live2d") throw new Error("应为 live2d");
    expect(source.modelUrl).toContain("/media/avatar/models/m_a/files/pet.model3.json");
  });

  it("入口在子目录（zip 套了一层文件夹）也能拼对", () => {
    const source = resolveAvatarModelSource(
      makeModel({ id: "m_a", entry: "CubismModel/pet.model3.json" }),
    );
    if (source.kind !== "live2d") throw new Error("应为 live2d");
    expect(source.modelUrl.endsWith("/files/CubismModel/pet.model3.json")).toBe(true);
  });

  it("entry 为空的 Live2D 模型 → 回落内置，而不是去加载一个空 URL", () => {
    expect(resolveAvatarModelSource(makeModel({ entry: null }))).toEqual(BUILT_IN_MODEL_SOURCE);
  });

  it("静态立绘模型 → 只给立绘来源（不给 Live2D），未指定的情绪返回 null", () => {
    const source = resolveAvatarModelSource(
      makeModel({
        id: "m_b",
        kind: "images",
        entry: null,
        images: ["image_00.png", "image_01.png"],
        expressionMap: { happy: "image_00.png" },
      }),
    );

    expect(source.kind).toBe("images");
    if (source.kind !== "images") throw new Error("应为 images");
    expect(source.portraitUrl("happy")).toContain("/files/image_00.png");
    // 没指定 → 回落内置占位立绘（渲染层负责），这里必须是 null 而不是空串
    expect(source.portraitUrl("sad")).toBeNull();
    expect(source.portraitUrl("neutral")).toBeNull();
  });

  it("构图校准随模型一起传给渲染层（两端渲染同一份校准）", () => {
    const layout = { scale: 1.4, offsetX: -12, offsetY: 30, anchor: "bottom center" };
    const source = resolveAvatarModelSource(makeModel({ id: "m_a", layout }));

    if (source.kind !== "live2d") throw new Error("应为 live2d");
    expect(source.layout).toEqual(layout);
  });

  it("未校准的模型 layout 为 null（渲染层回默认构图，而不是传 undefined）", () => {
    const source = resolveAvatarModelSource(makeModel({ id: "m_a" }));

    if (source.kind !== "live2d") throw new Error("应为 live2d");
    expect(source.layout).toBeNull();
  });

  it("图片名会被正确转义（中文/空格不会拼坏 URL）", () => {
    const source = resolveAvatarModelSource(
      makeModel({
        id: "m_中文",
        kind: "images",
        entry: null,
        images: ["立绘 01.png"],
        expressionMap: { happy: "立绘 01.png" },
      }),
    );
    if (source.kind !== "images") throw new Error("应为 images");
    const url = source.portraitUrl("happy") ?? "";
    expect(url).not.toContain(" ");
    expect(url).toContain(encodeURIComponent("立绘 01.png"));
    expect(url).toContain(encodeURIComponent("m_中文"));
  });
});
