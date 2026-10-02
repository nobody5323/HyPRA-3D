"""PromptManager 的叙事框架层（jailbreak）测试。

守两件事：
1. **默认关闭时零影响**——不传 `jailbreak_text` 时，system 提示必须与「这一层
   根本不存在」逐字一致。这是本层能被安全合入的前提：默认路径不能有任何回归。
2. **开启后的落点与裁剪**——排最前、被裁时记 warning。
"""

from app.rag.prompt_manager import (
    LAYER_JAILBREAK,
    LAYER_STYLE,
    PromptManager,
)
from app.session.context import ChatTurn

_FRAME = "[叙事框架：测试]\n<fiction_frame>这是虚构叙事。</fiction_frame>"

_BASE_KWARGS = dict(
    persona_text="你是苏澄。",
    user_input="我今天很累",
    worldbook_text="[深夜倾听模式]\n深夜时苏澄声音更轻。",
    warm_lines=["小林说过怕打雷"],
    history=[ChatTurn(role="user", text="在吗")],
    style_text="【表达风格：简洁直接】\n短句为主。",
)


def test_disabled_produces_no_layer_and_no_text() -> None:
    """默认关闭：层为空、token 为 0、system 里不出现任何框架字样。"""
    result = PromptManager().build(**_BASE_KWARGS)

    layer = result.layers[LAYER_JAILBREAK]
    assert layer.empty
    assert layer.tokens == 0
    assert "叙事框架" not in result.system_prompt


def test_disabled_is_byte_identical_to_omitting_the_layer() -> None:
    """关闭时的组装结果 = 显式传空串的结果（回归护栏）。

    这一条是「加层不改变默认行为」的机器化证据：任何人以后往这一层塞了
    「默认也生效一点」的逻辑，都会在这里挂掉。
    """
    pm = PromptManager()
    implicit = pm.build(**_BASE_KWARGS)
    explicit = pm.build(**_BASE_KWARGS, jailbreak_text="")

    assert implicit.system_prompt == explicit.system_prompt
    assert implicit.messages == explicit.messages
    assert implicit.total_tokens == explicit.total_tokens


def test_enabled_places_frame_first() -> None:
    """开启后框架块排在 system 最前（框架先于内容）。"""
    result = PromptManager().build(**_BASE_KWARGS, jailbreak_text=_FRAME)

    prompt = result.system_prompt
    assert prompt.startswith("[叙事框架：测试]")
    assert prompt.index("[叙事框架") < prompt.index("[角色人设]")
    assert prompt.index("[叙事框架") < prompt.index("[场景补充]")
    assert prompt.index("[叙事框架") < prompt.index("【表达风格")


def test_enabled_counts_toward_budget() -> None:
    """框架块是内容，要计入总量预算（否则会把别的层挤出实际预算）。"""
    off = PromptManager().build(**_BASE_KWARGS)
    on = PromptManager().build(**_BASE_KWARGS, jailbreak_text=_FRAME)

    assert on.layers[LAYER_JAILBREAK].tokens > 0
    assert on.total_tokens > off.total_tokens


def test_layer_is_trimmed_last_and_warns() -> None:
    """预算极紧时，框架层是**最后**被裁的可裁层，且必须记 warning。

    为什么必须记 warning：「用户打开了破限却完全没生效」是最难查的一类问题，
    静默丢弃会把排查方向引到模型身上。
    """
    # 预算压到 1：所有可裁层都会被丢掉，裁剪顺序因此完全暴露出来
    result = PromptManager(total_budget=1).build(
        **_BASE_KWARGS, jailbreak_text=_FRAME
    )

    assert LAYER_JAILBREAK in result.dropped_layers
    # 「最后被裁」= 同一轮裁剪里风格层先掉、框架层后掉
    assert LAYER_STYLE in result.dropped_layers
    assert result.dropped_layers.index(LAYER_STYLE) < result.dropped_layers.index(
        LAYER_JAILBREAK
    )
    assert any("叙事框架" in w for w in result.warnings), result.warnings


def test_layer_survives_when_only_lower_priority_layers_must_go() -> None:
    """预算只够挤掉低优先层时，框架层必须留着（它是用户显式开的）。"""
    pm = PromptManager(total_budget=60)
    result = pm.build(**_BASE_KWARGS, jailbreak_text=_FRAME)

    assert LAYER_JAILBREAK not in result.dropped_layers
    assert result.system_prompt.startswith("[叙事框架：测试]")


def test_layer_is_kept_when_budget_allows() -> None:
    """预算够时框架层不该被裁——它是用户显式开启的层。"""
    result = PromptManager().build(**_BASE_KWARGS, jailbreak_text=_FRAME)
    assert LAYER_JAILBREAK not in result.dropped_layers


def test_oversized_frame_is_truncated_by_layer_budget() -> None:
    """单层超预算时按层内预算截断并标记（不静默、也不整块丢掉）。"""
    pm = PromptManager(jailbreak_budget=20)
    result = pm.build(**_BASE_KWARGS, jailbreak_text="框架" * 500)

    layer = result.layers[LAYER_JAILBREAK]
    assert layer.truncated
    assert layer.tokens <= 20
