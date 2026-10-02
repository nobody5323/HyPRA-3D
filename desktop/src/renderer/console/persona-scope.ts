/**
 * 「当前陪伴对象是谁」的解析规则（纯逻辑，零运行时依赖）。
 *
 * 单独成模块有两个原因：
 *
 * 1. **可测**：这条规则出错的代价是「界面看起来选得好好的，实际把记忆写进了
 *    另一个对象」或者「对话直接 404」，必须能直接断言。放进 hook 里就只能靠
 *    渲染测试间接覆盖，而 desktop 的 vitest 跑在 node 环境、没有 DOM 基建。
 * 2. **唯一**：陪伴对象 id 同时是记忆隔离命名空间（`AGENTS.md §8.2` 的
 *    `companion:{id}`），控制台里三处都要用它（人设与文风 / 会话与记忆 /
 *    酒馆记忆导入）。规则只写一遍，就不会三处各飘各的。
 */

/**
 * 只描述用得到的形状，不 import `@/lib/api/types`。
 *
 * 这样本模块**没有任何 import**，测试可以直接引入，不必先给 vitest 配 alias；
 * 真实的 `PersonaCatalog` 结构上兼容，调用处不用转换。
 */
export interface PersonaCatalogLike {
  default_persona_id?: string;
  personas?: { id: string }[];
}

/**
 * 决定「当前陪伴对象」该用哪个 id。
 *
 * 优先级：用户偏好 > 部署声明 > 清单首项。
 *
 * 前两者都必须拿清单校验：偏好可能指向一个**已被删除**的角色（创作工坊里删过），
 * 部署声明也可能与清单不一致（配置写错）。不校验就会把一个后端不认识的 id
 * 发出去——对话直接 404，而界面上看起来毫无异样。
 */
export function resolvePersonaId(
  catalog: PersonaCatalogLike | null,
  preferenceId: string,
): string {
  const ids = (catalog?.personas ?? []).map((persona) => persona.id);
  const candidates = [preferenceId, catalog?.default_persona_id ?? ""];
  return candidates.find((id) => id && ids.includes(id)) ?? ids[0] ?? "";
}
