"""交互模式（AGENTS.md §8.1）：`companion` 桌宠对话 / `tavern` 酒馆聊天。

| 模式 | 定位 | 酒馆来源知识（世界书） |
| --- | --- | --- |
| `companion` | 跨会话长期陪伴 | **不**召回 |
| `tavern` | 还原酒馆原生体验 | 召回（`tavern` / `tavern:{persona_id}`） |

模式是**会话级属性**（落在 session 上），切换模式 = 新建会话——不做逐轮参数，
避免同一会话的上下文语义漂移。

**模式与提示词来源是正交的两个选择**：本模块只管「模式是什么、怎么定」，
组装策略由 `graph/nodes.assemble_prompt` 按 `st_preset` 是否存在分派
（两条独立路径，禁止 if/else 渗透进节点里）。所以「桌宠模式 + 酒馆预设」
是**合法组合**——用户明确要过（「桌宠模式也可以用酒馆预设」），
别把它当成错配去"修"。

目前模式的**行为落点只有一个**：知识检索是否纳入酒馆来源作用域
（见 `app/memory/knowledge/scopes`）。记忆作用域泛化（`companion:{id}` /
`session:{id}`）与 Summarize 分模式仍在 §8.5 的实施顺序里，未做。
"""

from __future__ import annotations

#: 桌宠对话模式（现状默认）：跨会话长期陪伴
MODE_COMPANION = "companion"
#: 酒馆聊天模式：还原酒馆原生体验
MODE_TAVERN = "tavern"

#: 全部合法模式
MODES = frozenset({MODE_COMPANION, MODE_TAVERN})


def normalize_mode(value: str | None) -> str:
    """把任意输入规范化为合法模式（空/非法一律回落到 `companion`）。

    刻意不抛错：模式来自请求参数与既有会话，一个手改坏的字段不该让用户
    连对话都发不出去——回落到默认模式即可（默认模式是功能最全的那个）。
    """
    text = (value or "").strip().lower()
    return text if text in MODES else MODE_COMPANION


def resolve_mode(
    *,
    explicit: str | None = None,
    session_mode: str | None = None,
    st_preset_id: str | None = None,
) -> str:
    """定出本轮模式，优先级：显式请求 > 由 ST 预设推断 > 会话既有 > 桌宠。

    **模式与酒馆预设是正交的两个选择，不要把它们绑成因果**：

    | 选择 | 管什么 |
    | --- | --- |
    | 模式 | 记忆作用域与召回范围（酒馆来源知识只在本模式的 `tavern` 作用域里查） |
    | 酒馆预设（`st_preset_id`） | 提示词走哪条组装路径（内置分层 / ST 预设） |

    所以「桌宠模式 + 挂着酒馆预设」是**合法组合**：提示词按酒馆预设组装，但酒馆
    世界书不参与召回。用户明确要过这个组合（「桌宠模式也可以用酒馆预设」），
    别把它当错配去"修"。

    那第二级（由预设推断）为什么还要存在：它是**没选过模式时**的兜底，用来维持
    引入显式开关之前的行为——那时「选了酒馆预设」是酒馆模式的唯一信号，
    推断成 `tavern` 才不至于让酒馆世界书突然全召不回来。一旦请求带了 `mode`
    （界面上的显式开关，见 §8.1），第一级生效，本推断不再参与。

    会话上的 `mode` 是**记录**与该开关的载体（也兼容「显式建成 tavern 的会话在
    没选预设时继续按 tavern 处理」）。
    """
    if (explicit or "").strip().lower() in MODES:
        return normalize_mode(explicit)
    if (st_preset_id or "").strip():
        return MODE_TAVERN
    if (session_mode or "").strip().lower() in MODES:
        return normalize_mode(session_mode)
    return MODE_COMPANION
