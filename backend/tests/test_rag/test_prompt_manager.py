"""PromptManager 测试：分层顺序、预算裁剪、优先级、必留层。"""

from app.rag.prompt_manager import (
    LAYER_FACTS,
    LAYER_HISTORY,
    LAYER_KNOWLEDGE,
    LAYER_PERSONA,
    LAYER_SKILLS,
    LAYER_USER,
    LAYER_WARM,
    LAYER_WORLDBOOK,
    PromptManager,
)
from app.session.context import ChatTurn


def _history(n: int) -> list[ChatTurn]:
    turns: list[ChatTurn] = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        turns.append(ChatTurn(role=role, text=f"第{i}条历史消息"))
    return turns


def test_layers_order_in_system_prompt() -> None:
    """system 内顺序：人设 → 世界书 → 记忆块。"""
    pm = PromptManager()
    result = pm.build(
        persona_text="你是苏澄。",
        user_input="我今天很累",
        worldbook_text="[深夜倾听模式]\n深夜时苏澄声音更轻。",
        warm_lines=["小林说过怕打雷"],
        fact_lines=["小林 喜欢 下雨天"],
    )
    prompt = result.system_prompt
    assert prompt.index("[角色人设]") < prompt.index("[场景补充]")
    assert prompt.index("[场景补充]") < prompt.index("[记忆回忆]")


def test_memory_block_inner_order() -> None:
    """记忆块内部顺序：相关回忆 → 已知事实。"""
    pm = PromptManager()
    result = pm.build(
        persona_text="人设",
        user_input="聊聊",
        warm_lines=["回忆 A"],
        fact_lines=["事实 B"],
    )
    block = result.memory_block
    assert block.index("相关回忆") < block.index("已知事实")


def test_knowledge_layer_order_in_system_prompt() -> None:
    """system 内顺序：人设 → 世界书 → 参考资料 → 记忆回忆。"""
    pm = PromptManager()
    result = pm.build(
        persona_text="你是苏澄。",
        user_input="咨询室在哪里",
        worldbook_text="[深夜倾听模式]\n深夜时苏澄声音更轻。",
        knowledge_lines=["咨询室在老城区一栋二层小楼里。"],
        warm_lines=["小林说过怕打雷"],
    )
    prompt = result.system_prompt

    assert (
        prompt.index("[角色人设]")
        < prompt.index("[场景补充]")
        < prompt.index("[参考资料]")
        < prompt.index("[记忆回忆]")
    )


def test_knowledge_layer_omitted_when_empty() -> None:
    """无知识库内容时不注入空块。"""
    result = PromptManager().build(persona_text="人设", user_input="x")

    assert "[参考资料]" not in result.system_prompt
    assert result.layers[LAYER_KNOWLEDGE].empty


def test_knowledge_dropped_before_worldbook() -> None:
    """总量不足时按优先级裁剪：知识库(70) 先于世界书(80) 被裁。"""
    pm = PromptManager(total_budget=60)
    result = pm.build(
        persona_text="人设",
        user_input="问",
        worldbook_text="世界书内容" * 10,
        knowledge_lines=["参考资料内容" * 10],
    )

    assert LAYER_KNOWLEDGE in result.dropped_layers
    assert LAYER_WORLDBOOK not in result.dropped_layers


def test_knowledge_budget_truncates_lines() -> None:
    """层内预算生效：超长内容被截断并标记。"""
    pm = PromptManager(knowledge_budget=20)
    result = pm.build(
        persona_text="人设",
        user_input="问",
        knowledge_lines=["参考资料" * 50],
    )

    assert result.layers[LAYER_KNOWLEDGE].truncated


def test_messages_structure() -> None:
    pm = PromptManager()
    result = pm.build(
        persona_text="人设",
        user_input="最后一问",
        history=_history(4),
    )
    msgs = result.messages
    assert msgs[0]["role"] == "system"
    assert msgs[-1] == {"role": "user", "content": "最后一问"}
    assert [m["role"] for m in msgs[1:-1]] == ["user", "assistant", "user", "assistant"]


def test_empty_layers_omitted() -> None:
    """无内容的层不应出现在 system 中。"""
    pm = PromptManager()
    result = pm.build(persona_text="只有人设", user_input="你好")
    assert "[场景补充]" not in result.system_prompt
    assert "[记忆回忆]" not in result.system_prompt
    assert result.memory_block == ""


def test_worldbook_budget_truncates() -> None:
    long_wb = "世界书内容" * 200
    pm = PromptManager(worldbook_budget=50)
    result = pm.build(persona_text="人设", user_input="问", worldbook_text=long_wb)
    assert result.layers[LAYER_WORLDBOOK].truncated is True
    assert result.layers[LAYER_WORLDBOOK].tokens <= 50


def test_history_budget_keeps_recent() -> None:
    pm = PromptManager(history_budget=40)
    result = pm.build(persona_text="人设", user_input="问", history=_history(20))
    kept = result.history
    assert 0 < len(kept) < 20
    assert kept[-1].text == "第19条历史消息"  # 保留最新的


def test_total_budget_drops_low_priority_first() -> None:
    """总量不足时：先弃最低优先层（事实），保人设与本次输入。"""
    pm = PromptManager(total_budget=60)
    result = pm.build(
        persona_text="你是苏澄，温柔的心理倾听师。",
        user_input="我想聊聊",
        fact_lines=["事实" * 40],
    )
    assert LAYER_FACTS in result.dropped_layers
    # 必留层仍在
    assert result.layers[LAYER_PERSONA].body
    assert result.layers[LAYER_USER].body == "我想聊聊"
    assert any("已裁减" in w for w in result.warnings)


def test_mandatory_layers_never_dropped() -> None:
    """即使预算极端小，人设与本次输入也必须保留。"""
    pm = PromptManager(total_budget=1)
    result = pm.build(persona_text="人设必须保留", user_input="输入必须保留")
    assert result.layers[LAYER_PERSONA].body == "人设必须保留"
    assert result.layers[LAYER_USER].body == "输入必须保留"


def test_layer_tokens_recorded() -> None:
    pm = PromptManager()
    result = pm.build(persona_text="人设内容", user_input="问", warm_lines=["回忆一条"])
    assert result.layers[LAYER_WARM].tokens > 0
    assert result.total_tokens > 0


def test_history_layer_tracks_after_trim() -> None:
    """历史层 token 统计应随裁剪同步更新。"""
    pm = PromptManager(history_budget=30)
    result = pm.build(persona_text="人设", user_input="问", history=_history(10))
    expected = sum(len(t.text) * 0.7 for t in result.history)
    assert result.layers[LAYER_HISTORY].tokens <= expected + 5


# ---------- 可用技能层（§9.6）----------

SKILLS = "- crisis：情绪危机安抚 —— 用户表达绝望时"


def test_skills_layer_sits_between_worldbook_and_knowledge() -> None:
    """技能是「能力说明」而非「记忆」，所以紧跟世界书、在记忆层之前。"""
    pm = PromptManager()
    result = pm.build(
        persona_text="你是苏澄。",
        user_input="我今天很累",
        worldbook_text="[深夜倾听模式]",
        skills_text=SKILLS,
        knowledge_lines=["用户上传的资料"],
        warm_lines=["回忆一条"],
    )
    prompt = result.system_prompt

    assert prompt.index("[场景补充]") < prompt.index("[可用技能]")
    assert prompt.index("[可用技能]") < prompt.index("[参考资料]")
    # 不混进记忆块——否则「该怎么做」与「知道什么」会互相冲淡
    assert "[可用技能]" not in result.memory_block


def test_skills_layer_omitted_when_empty() -> None:
    pm = PromptManager()
    result = pm.build(persona_text="人设", user_input="问", skills_text="")

    assert "[可用技能]" not in result.system_prompt
    assert result.layers[LAYER_SKILLS].empty


def test_skills_budget_truncates() -> None:
    pm = PromptManager(skills_budget=20)
    result = pm.build(
        persona_text="人设",
        user_input="问",
        skills_text="\n".join(f"- skill-{i}：名称 —— 适用场景描述" for i in range(20)),
    )

    assert result.layers[LAYER_SKILLS].truncated is True
    assert "skill-19" not in result.system_prompt


def test_skills_dropped_after_knowledge() -> None:
    """总量不足时的裁剪次序：记忆层（priority 70）先于技能层（75）被舍。

    技能是「该怎么做」，比「关于这个人知道什么」更靠近人设；预算不够时宁可
    少注入几条记忆，也要让模型知道遇到某些情况该采取什么流程。
    """
    pm = PromptManager(total_budget=60)
    result = pm.build(
        persona_text="人设",
        user_input="问",
        skills_text=SKILLS,
        knowledge_lines=["资料" * 20],
        worldbook_text="场景补充" * 20,
    )

    assert LAYER_SKILLS in result.dropped_layers, result.dropped_layers
    assert result.dropped_layers.index(LAYER_KNOWLEDGE) < result.dropped_layers.index(
        LAYER_SKILLS
    )
