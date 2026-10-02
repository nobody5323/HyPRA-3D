import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import type { PersonaCatalog } from "@/lib/api/types";
import {
  PERSONA_NAME_FALLBACK,
  personaName,
  personaTitle,
} from "@/lib/chat/persona";

/** 测试夹具：两个风格完全不同的内置人设（咨询型只是其中之一） */
const CATALOG: PersonaCatalog = {
  default_persona_id: "therapist-elder-sister",
  personas: [
    {
      id: "therapist-elder-sister",
      name: "苏澄",
      title: "心理倾听师 · 温柔年长的知心姐姐",
      description: "",
      tags: [],
    },
    {
      id: "energetic-roommate",
      name: "陈小满",
      title: "拉你出门的合租室友",
      description: "",
      tags: [],
    },
  ],
};

describe("personaName / personaTitle", () => {
  it("按 id 取展示名与一句话定位", () => {
    expect(personaName(CATALOG, "energetic-roommate")).toBe("陈小满");
    expect(personaTitle(CATALOG, "energetic-roommate")).toBe("拉你出门的合租室友");
  });

  it("清单未加载或 id 查不到时用中性兜底词，不退回某个具体角色名", () => {
    expect(personaName(null, "energetic-roommate")).toBe(PERSONA_NAME_FALLBACK);
    expect(personaName(CATALOG, "user-nobody")).toBe(PERSONA_NAME_FALLBACK);
    // 兜底词本身不能是任何内置角色名，否则「换人设还显示苏澄」会原样复现
    expect(CATALOG.personas.map((persona) => persona.name)).not.toContain(
      PERSONA_NAME_FALLBACK,
    );
  });

  it("定位查不到时返回空串（由调用方决定要不要渲染那一行）", () => {
    expect(personaTitle(null, "energetic-roommate")).toBe("");
    expect(personaTitle(CATALOG, "user-nobody")).toBe("");
  });
});

/** vitest 从 frontend/ 运行（同 live2d-model-integration.test.ts 的做法） */
const ROOT = process.cwd();

const UI_DIRS = [
  path.join(ROOT, "components"),
  path.join(ROOT, "app"),
  // 桌宠窗与程序控制台复用同一批组件，一并纳入
  path.join(ROOT, "..", "desktop", "src", "renderer"),
];

/**
 * 只检查真会渲染出去的代码。
 *
 * 注释里提到角色名（解释这段历史为什么这么写）不算硬编码，所以先剥掉注释。
 */
function stripComments(code: string): string {
  return code.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

function collectSourceFiles(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...collectSourceFiles(full));
    else if (/\.tsx?$/.test(entry.name)) files.push(full);
  }
  return files;
}

describe("界面不硬编码角色名", () => {
  /**
   * 曾经写死在界面里的两个名字：
   * - 「苏澄」：默认人设的角色名（人设只是示例，换角色后不能还显示它）；
   * - 「小林」：默认的用户称呼（用户叫什么由他自己在偏好里设）。
   *
   * 两者都应当由数据传进来（人设清单 / 共享偏好），不写进组件源码。
   */
  const HARDCODED_NAMES = ["苏澄", "小林"];

  for (const name of HARDCODED_NAMES) {
    it(`前端 UI 与桌宠渲染层的代码里没有「${name}」`, () => {
      const offenders = UI_DIRS.flatMap(collectSourceFiles).filter((file) =>
        stripComments(readFileSync(file, "utf8")).includes(name),
      );
      expect(offenders.map((file) => path.relative(ROOT, file))).toEqual([]);
    });
  }
});
