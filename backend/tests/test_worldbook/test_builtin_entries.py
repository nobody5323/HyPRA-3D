"""内置世界书条目的归属一致性。

内置条目按**内容归属**：写某个内置角色生活/设定的条目填该角色 id，
与角色无关的通用条目才留 `*`（`WorldBookEntry.scope`）。

这组测试守两件事：

1. 归属 id 必须指向**真实存在**的人设——写错一个字母的后果是条目永不触发，
   而「条目没生效」在对话里没有任何报错，只能在这里拦住；
2. 苏澄的三条设定确实只对她生效。否则用户换个人设就会看见「苏澄的猫」
   与「苏澄的咨询室」——产品口径是「苏澄只是一个例子，不是整个项目的风格」。
"""

from app.prompts.persona.loader import load_builtin_presets
from app.worldbook.loader import load_builtin_entries
from app.worldbook.matcher import match_entries
from app.worldbook.models import SCOPE_ALL

#: 写苏澄专属设定的条目（改动这些 id 时同步这里）
SU_CHENG_ENTRIES = ("consulting-room", "night-mode", "pet-cat")

SU_CHENG = "therapist-elder-sister"


def test_scoped_entry_points_to_existing_persona() -> None:
    """非全局条目的归属必须是一个真实存在的人设 id（防拼写错误导致静默失效）。"""
    persona_ids = set(load_builtin_presets())
    for entry in load_builtin_entries():
        if entry.scope == SCOPE_ALL:
            continue
        assert entry.scope in persona_ids, (
            f"条目 {entry.id} 的归属「{entry.scope}」不是任何内置人设的 id，"
            f"它永远不会被注入"
        )


def test_su_cheng_entries_are_scoped_to_her() -> None:
    """苏澄的设定归属苏澄，不挂在 `*` 上。"""
    entries = {entry.id: entry for entry in load_builtin_entries()}
    for entry_id in SU_CHENG_ENTRIES:
        assert entries[entry_id].scope == SU_CHENG


def test_su_cheng_entries_do_not_leak_to_other_personas() -> None:
    """换别的人设时，苏澄的条目不会被注入（选「陈小满」不该看见苏澄的猫）。"""
    entries = load_builtin_entries()
    hits = match_entries(entries, "我家猫今天特别黏人", companion_id="energetic-roommate")
    assert [entry.id for entry in hits] == []


def test_su_cheng_entries_still_fire_for_her() -> None:
    """同一句话在苏澄这里照常触发（归属过滤没把功能一起关掉）。"""
    entries = load_builtin_entries()
    hits = match_entries(entries, "我家猫今天特别黏人", companion_id=SU_CHENG)
    assert "pet-cat" in [entry.id for entry in hits]
