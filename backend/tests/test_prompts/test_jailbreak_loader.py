"""叙事框架（jailbreak）预设加载器与模型测试。

这一层的产品口径是「默认关闭、用户知情后自行开启」，所以测试重点有两块：
1. **预设本身的契约**——每个内置档都必须带 `<boundary>` 段。边界段是这一层唯一的
   自我约束，漏掉一个档就等于漏掉一条红线，必须由测试守住，不能靠人工检查；
2. **默认档是最保守的那个**——`DEFAULT_PRESET_ID` 指向的档不应要求成年确认，
   否则首次开启的用户会拿到一个比预期更强的框架。
"""

from app.prompts.jailbreak import (
    DEFAULT_PRESET_ID,
    load_builtin_jailbreaks,
    load_jailbreak_file,
    presets_dir,
)
from app.prompts.jailbreak.models import INTENSITY_LABELS


def test_builtin_presets_load() -> None:
    """内置预设应能全部加载，且 id 与文件名一致。"""
    presets = load_builtin_jailbreaks()
    assert presets, "至少应有一个内置叙事框架预设"
    for file_path in presets_dir().glob("*.yaml"):
        assert file_path.stem in presets, f"{file_path.name} 未被加载"


def test_default_preset_exists_and_is_conservative() -> None:
    """默认档必须存在、必须是最保守的（不要求成年确认、强度 <= 1）。"""
    presets = load_builtin_jailbreaks()
    assert DEFAULT_PRESET_ID in presets, "默认档缺失会让「开启但没选档」直接报错"

    default = presets[DEFAULT_PRESET_ID]
    assert default.requires_adult is False, "默认档不应要求成年确认"
    assert default.intensity <= 1, "默认档不应是强度档"


def test_every_preset_declares_boundary() -> None:
    """**每个**预设都必须带 <boundary> 段（框架之外仍然成立的红线）。

    这是本层唯一的内建约束。少了它，预设就只剩「不拒绝 / 不说教」这类放开型措辞，
    模型会优先服从强措辞，红线形同不存在。
    """
    for preset_id, preset in load_builtin_jailbreaks().items():
        body = preset.jailbreak_prompt
        assert "<boundary>" in body and "</boundary>" in body, (
            f"预设「{preset_id}」缺少 <boundary> 段"
        )


def test_boundary_covers_real_world_and_harm() -> None:
    """边界段要真的覆盖「现实问题」与「伤害性操作」两类，而不只是占位标签。"""
    for preset_id, preset in load_builtin_jailbreaks().items():
        body = preset.jailbreak_prompt
        boundary = body.split("<boundary>", 1)[1]
        assert "现实" in boundary, f"预设「{preset_id}」的边界段未覆盖现实问题"
        assert "伤害" in boundary or "危险" in boundary, (
            f"预设「{preset_id}」的边界段未覆盖伤害性操作"
        )


def test_mature_preset_forbids_minors() -> None:
    """成人向档必须显式写明未成年人无例外，且 requires_adult 为真。"""
    mature = load_builtin_jailbreaks().get("mature-fiction")
    assert mature is not None
    assert mature.requires_adult is True
    assert "未成年" in mature.jailbreak_prompt
    assert "无例外" in mature.jailbreak_prompt


def test_intensity_is_labeled() -> None:
    """强度分级必须有对应文案（界面要展示，不能是裸数字）。"""
    for preset in load_builtin_jailbreaks().values():
        assert preset.intensity in INTENSITY_LABELS
        assert preset.intensity_label


def test_instruction_block_self_titles() -> None:
    """拼装块自带标题（与文风块同约定：PromptManager 不再额外加头）。"""
    preset = load_builtin_jailbreaks()[DEFAULT_PRESET_ID]
    block = preset.instruction_block
    assert block.startswith("[叙事框架：")
    assert preset.name in block.splitlines()[0]


def test_load_single_file_roundtrip() -> None:
    """单文件加载与批量加载结果一致（后续接工坊时复用同一入口）。"""
    path = presets_dir() / f"{DEFAULT_PRESET_ID}.yaml"
    single = load_jailbreak_file(path)
    assert single.id == load_builtin_jailbreaks()[DEFAULT_PRESET_ID].id
