import { describe, expect, it } from "vitest";

import { resolvePersonaId } from "../src/renderer/console/persona-scope";

/**
 * 控制台「当前陪伴对象」的解析规则。
 *
 * 为什么值得单独测：这个 id 同时是记忆隔离命名空间（`AGENTS.md §8.2` 的
 * `companion:{id}`）。解析错了有两种现场表现，而且都不容易发现——
 *
 * - 选中的角色**已被删除**（创作工坊里删过）却照原样发出去 → 对话 404，
 *   界面上看不出任何异样；
 * - 控制台三处（人设与文风 / 会话与记忆 / 酒馆记忆导入）各算各的 →
 *   用户点的「导入到苏澄」实际写进了别人名下。
 *
 * 真正的导火索是第三种：`ConsoleApp` 把**数字人模型 id** 当 companionId 传给了
 * 酒馆导入面板，模型没选时那个值是空串，于是面板退化成一个点不动的死界面。
 * 根因就是「当前陪伴对象」在控制台里没有唯一来源——本模块是那个唯一来源。
 */

const CATALOG = {
  default_persona_id: "builtin-a",
  personas: [{ id: "builtin-a" }, { id: "builtin-b" }, { id: "tbp-custom" }],
};

describe("resolvePersonaId", () => {
  it("用户偏好有效时优先用它", () => {
    expect(resolvePersonaId(CATALOG, "tbp-custom")).toBe("tbp-custom");
  });

  it("偏好为空时用部署声明的默认", () => {
    expect(resolvePersonaId(CATALOG, "")).toBe("builtin-a");
  });

  it("偏好指向已删除的角色时退回部署默认，不发一个后端不认识的 id", () => {
    // 这是最容易漏的一种：用户删了角色，偏好文件里还留着它的 id
    expect(resolvePersonaId(CATALOG, "已删除的角色")).toBe("builtin-a");
  });

  it("偏好与部署默认都失效时用清单首项", () => {
    const catalog = { default_persona_id: "也没了", personas: [{ id: "只有这个" }] };
    expect(resolvePersonaId(catalog, "已删除的角色")).toBe("只有这个");
  });

  it("清单为空时返回空串（让调用方去处理「还没有可选对象」）", () => {
    expect(resolvePersonaId({ personas: [] }, "任意")).toBe("");
    expect(resolvePersonaId(null, "任意")).toBe("");
  });

  it("清单缺失 personas 字段时同样安全", () => {
    expect(resolvePersonaId({ default_persona_id: "x" }, "x")).toBe("");
  });
});
