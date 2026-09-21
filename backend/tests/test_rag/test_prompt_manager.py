"""PromptManager 测试：分层顺序、预算裁剪、优先级、必留层。"""

from app.rag.prompt_manager import (
    LAYER_FACTS,
    LAYER_HISTORY,
    LAYER_PERSONA,
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
