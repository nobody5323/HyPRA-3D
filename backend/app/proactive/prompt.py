"""主动开口的提示词片段（合成内部消息 + 行为约束块）。

设计见 `docs/proactive-multimodal.md` §5.4。**硬约定**：主动消息走**同一条**
`chat_graph`，不另起炉灶——另写一套生成 = 人格漂移 + 两处维护。

因此这里只做两件事：
1. 合成一条**内部输入**（`user_input`），它承载「触发原因」；
2. 产出一段**行为约束块**（进 system），把「这不是用户说的话」说清楚，
   并收紧形态（更短、不追问、可以不说）。

第 2 条是必需的：图里 `user_input` 会被当成最后一条 user 消息，
如果不在 system 里声明「上面那条不是用户说的」，模型会以为用户
真的讲了「触发原因：用户凌晨 2 点还在活动」这种话。
"""

from __future__ import annotations

from app.proactive.models import ProactiveIntent

#: 主动开口块的标题（进 system）
PROACTIVE_SECTION = "本轮说明"

#: 模型可以用这个标记表示「此刻不想说话」。
#:
#: 允许模型拒绝，是这一层最容易被忽略的设计：一个**必须**说点什么的陪伴者，
#: 迟早会说废话。给出「可以不说」的自由度之后，「开口」才是有意义的动作。
DECLINE_MARKER = "[不说]"


def build_proactive_input(intent: ProactiveIntent) -> str:
    """合成内部输入（进 `ChatState.user_input`）。

    刻意保留 `[系统触发·非用户输入]` 前缀：即使约束块因为预算被裁掉，
    模型也还有一次机会看出这不是用户说的话。
    """
    lines = [
        "[系统触发·非用户输入]",
        f"触发原因：{intent.reason}",
    ]
    if intent.facts:
        lines.append("你此刻感知到的事实：")
        lines.extend(f"- {fact}" for fact in intent.facts)
    lines.append("请以你的身份，自然地开口说一句。")
    return "\n".join(lines)


def build_proactive_block(*, reason: str = "", facts: tuple[str, ...] = (), char_limit: int = 60) -> str:
    """产出主动开口的行为约束块（进 system 的 `LAYER_PROACTIVE`）。

    参数:
        reason: 触发原因。**只用于日志与调试**——刻意不写进块里：
            约束块是**行为说明**，把「用户凌晨 2 点还在活动」复述一遍，
            等于给模型第二次机会去念触发原因（第一次在 `user_input` 里）。
        char_limit: 本次开口的字数上限。比常规回复更紧——主动搭话不该是独白。
            **刻意走提示词而不是 PromptManager 的构造参数**：后者是编译期快照，
            为一个逐轮变化的值去重建整张图（世界书还要重新编码向量）不值得。
    """
    limit = max(10, int(char_limit))
    return (
        f"【{PROACTIVE_SECTION}】\n"
        "这一轮**没有用户消息**：上面那条以「[系统触发·非用户输入]」开头的不是用户说的话，"
        "而是你此刻注意到的情形。\n"
        "请以你的身份主动开口，像刚好想到他、想跟他说一句。\n"
        "\n"
        "要求：\n"
        f"- 只发一条消息，不超过 {limit} 字。\n"
        "- 像搭话，不像通知：不要用「我注意到你在…」这类系统口吻，也不要复述触发原因。\n"
        "- **不要追问**，不要连着抛问题——主动开口是递一句话，不是发起一场对话。\n"
        "- 给用户不回应的余地：说完这句就停下。\n"
        f"- 如果此刻你确实没什么想说的，就只回 {DECLINE_MARKER}（这一条会被系统识别，"
        "不会发给用户，也不会留下记录）。\n"
    )


def is_declined(reply: str) -> bool:
    """回复是否表示「此刻不想说话」。

    两种形态都算：显式标记，以及空回复（模型把 token 花在思考上）。
    后者必须一起算——否则「空回复」会被当成一条正常消息推给前端，
    界面上出现一个**空气泡**，而那正是 `api/chat.py` ⑤ 花力气避免的东西。
    """
    text = (reply or "").strip()
    return not text or text == DECLINE_MARKER or text.startswith(DECLINE_MARKER)
