"""tavern-bridge 插件测试：PNG 角色卡解析、世界书映射、会话读取、只读强制、端到端加载。

**夹具全部自创**（`AGENTS.md §8.6` 红线）：不使用任何真实角色卡 / 世界书内容，
只复刻它们的**结构**（V1 空字段、双 chunk、base64、常驻条目、深度注入…）。
"""

from __future__ import annotations

import base64
import json
import struct
import zlib
from pathlib import Path

import pytest

from app.plugins.capabilities import PluginState
from app.plugins.context import PluginContext
from app.plugins.manifest import PluginManifest
from app.plugins.registry import PluginRegistry

# 插件是单文件模块，从 backend/plugins 动态加载，测试里按路径导入以便直接测内部函数
import importlib.util

_PLUGIN_PATH = Path(__file__).resolve().parents[2] / "plugins" / "tavern-bridge" / "plugin.py"


def _load_plugin_module():
    spec = importlib.util.spec_from_file_location("tavern_bridge_under_test", _PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tb():
    """被测插件模块（module 级：加载一次即可）。"""
    return _load_plugin_module()


# =============================================================
# 合成夹具（自创内容）
# =============================================================


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def make_card_png(card: dict, *, key: str = "ccv3", ztxt: bool = False) -> bytes:
    """构造含角色卡数据的 PNG（1×1，仅用于解析测试）。"""
    payload = base64.b64encode(json.dumps(card, ensure_ascii=False).encode("utf-8"))

    if ztxt:
        text_chunk = _png_chunk(
            b"zTXt", key.encode("latin-1") + b"\x00\x00" + zlib.compress(payload)
        )
    else:
        text_chunk = _png_chunk(b"tEXt", key.encode("latin-1") + b"\x00" + payload)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, RGB
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + text_chunk
        + _png_chunk(b"IEND", b"")
    )


@pytest.fixture
def tavern_dir(tmp_path: Path) -> Path:
    """合成一个酒馆用户数据目录（自创内容）。"""
    root = tmp_path / "SillyTavern" / "data" / "default-user"
    (root / "worlds").mkdir(parents=True)
    (root / "characters").mkdir()
    (root / "chats" / "测试角色").mkdir(parents=True)
    return root


# =============================================================
# PNG 角色卡解析
# =============================================================


def test_png_text_chunks_reads_both_keys(tb) -> None:
    """`chara`(V2) 与 `ccv3`(V3) 都能取出；两个值都是 base64。"""
    v2 = {"spec": "chara_card_v2", "data": {"name": "旧版角色"}}
    png_v2 = make_card_png(v2, key="chara")
    chunks = tb.png_text_chunks(png_v2)
    assert json.loads(base64.b64decode(chunks["chara"]))["data"]["name"] == "旧版角色"


def test_ccv3_takes_precedence_over_chara(tb) -> None:
    """V3 优先于 V2（与 SillyTavern 的读取优先级一致）。"""
    png_a = make_card_png({"data": {"name": "V2 角色"}}, key="chara")
    png_b = make_card_png({"data": {"name": "V3 角色"}}, key="ccv3")
    # 把两个 chunk 拼进同一个 PNG
    merged = png_a[:-12] + png_b[8 + 25 :]  # 去掉 b 的签名与 IHDR，保留其 tEXt+IEND
    card = tb.read_character_card(merged)
    assert card is not None
    assert card["name"] == "V3 角色"


def test_character_card_parses_ccv3(tb) -> None:
    card = tb.read_character_card(make_card_png({"data": {"name": "新角色"}}))
    assert card == {"name": "新角色"}


def test_ztxt_chunk_supported(tb) -> None:
    """压缩文本块（zTXt）同样可读。"""
    card = tb.read_character_card(
        make_card_png({"data": {"name": "压缩卡"}}, ztxt=True)
    )
    assert card is not None
    assert card["name"] == "压缩卡"


def test_no_embedded_data_returns_none(tb) -> None:
    png_without_card = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IEND", b"")
    assert tb.read_character_card(png_without_card) is None


def test_non_png_rejected(tb) -> None:
    with pytest.raises(ValueError, match="不是 PNG"):
        tb.read_character_card(b"not a png at all")


def test_v1_fields_backfilled_from_top_level(tb) -> None:
    """★ 勘察约束 2：V3 的 data.description 常为空，正文在顶层——必须回填。"""
    card = {
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        # 顶层 V1 字段（真实卡就是在这里放正文）
        "name": "顶层名字",
        "description": "顶层正文设定",
        "personality": "顶层性格",
        "first_mes": "顶层开场白",
        "data": {
            "name": "顶层名字",
            "description": "",  # ← 实测为空
            "personality": "",
            "first_mes": "",
            "creator": "作者甲",
        },
    }

    merged = tb.merge_v1_fields(card)

    assert merged["description"] == "顶层正文设定"
    assert merged["personality"] == "顶层性格"
    assert merged["first_mes"] == "顶层开场白"
    assert merged["creator"] == "作者甲"          # data 里已有的不被覆盖


def test_v1_backfill_does_not_override_existing_data(tb) -> None:
    """data 里已有非空值时，顶层不覆盖（V3 优先）。"""
    card = {"description": "顶层旧值", "data": {"description": "V3 新值"}}
    assert tb.merge_v1_fields(card)["description"] == "V3 新值"


# =============================================================
# 世界书条目映射
# =============================================================


def test_entries_mapped_from_st_shape(tb) -> None:
    """真实形状（勘察所得）→ 中性条目：常驻 + at_depth + 概率 + 关闭开关。"""
    book = {
        "entries": {
            "0": {
                "uid": 7,
                "comment": "角色设定",
                "content": "设定正文",
                "key": ["关键词甲", "关键词乙"],
                "constant": True,
                "position": 4,
                "depth": 4,
                "order": 10,
                "disable": False,
                "caseSensitive": True,
                "probability": 80,
                "group": "分组甲",          # 未知字段 → extra 透传
                "excludeRecursion": True,
            }
        }
    }

    entries = tb.entries_from_book(book, source_id="world/demo")

    assert len(entries) == 1
    entry = entries[0]
    assert entry.title == "角色设定"
    assert entry.content == "设定正文"
    assert entry.keys == ("关键词甲", "关键词乙")
    assert entry.constant is True
    assert entry.position == "at_depth"        # 4 → at_depth
    assert entry.depth == 4
    assert entry.order == 10
    assert entry.enabled is True
    assert entry.case_sensitive is True
    assert entry.probability == 80
    assert entry.source_id == "world/demo#7"
    assert entry.extra["group"] == "分组甲"     # 来源特有字段透传
    assert entry.extra["excludeRecursion"] is True


def test_disabled_entry_mapped_as_disabled(tb) -> None:
    book = {"entries": [{"content": "x", "disable": True}]}
    assert tb.entries_from_book(book, source_id="w")[0].enabled is False


def test_title_falls_back_to_first_key_then_index(tb) -> None:
    book = {"entries": [{"content": "甲", "key": ["首个关键词"]}, {"content": "乙"}]}
    entries = tb.entries_from_book(book, source_id="w")
    assert entries[0].title == "首个关键词"
    assert entries[1].title == "条目 2"


def test_entries_tolerate_bad_shapes(tb) -> None:
    """坏数据不抛异常：非字典、空正文、未知 position 都安全降级。"""
    book = {
        "entries": [
            "不是字典",
            {"content": "", "key": []},                    # 空正文 → 丢弃
            {"content": "有正文", "position": "bad"},       # 非法 position → after_char
            {"content": "概率越界", "probability": 999},    # 截断到 100
        ]
    }
    entries = tb.entries_from_book(book, source_id="w")
    assert len(entries) == 2
    assert entries[0].position == "after_char"
    assert entries[1].probability == 100


def test_entries_from_missing_book(tb) -> None:
    assert tb.entries_from_book(None, source_id="w") == []
    assert tb.entries_from_book({}, source_id="w") == []


def test_character_mapped_with_greetings_and_tags(tb) -> None:
    card = {
        "name": "示例角色",
        "description": "设定",
        "alternate_greetings": ["问候一", "", "问候二"],
        "tags": ["标签甲"],
        "mes_example": "<START>\n示例对话",
    }
    character = tb.character_from_card(card, avatar_path="/x/a.png", source_id="a")

    assert character.name == "示例角色"
    assert character.alternate_greetings == ("问候一", "问候二")   # 空串被剔除
    assert character.extra["tags"] == ["标签甲"]
    assert character.example_messages == "<START>\n示例对话"
    assert character.source == "tavern-bridge"


# =============================================================
# 数据源（合成目录）
# =============================================================


def _ctx_for(tmp_path: Path, tavern_dir: Path, *, write: bool = False) -> PluginContext:
    from app.plugins.capabilities import FilesystemPermission, Permission

    manifest = PluginManifest(
        id="tavern-bridge",
        display_name="酒馆数据接入",
        permissions=Permission(
            filesystem=FilesystemPermission(read=[str(tavern_dir)], write=write)
        ),
    )
    return PluginContext(
        manifest=manifest, settings={"tavern_dir": str(tavern_dir)}, data_dir=tmp_path
    )


@pytest.fixture
def populated_tavern(tmp_path: Path) -> Path:
    """装满合成数据的酒馆目录（标准 ST 三层结构：根 / data / profile）。

    结构：1 本世界书（2 条）+ 1 张角色卡（含内嵌书）+ 1 个会话。
    """
    root = tmp_path / "SillyTavern" / "data" / "default-user"
    (root / "worlds").mkdir(parents=True)
    (root / "characters").mkdir()
    (root / "chats" / "示例角色").mkdir(parents=True)

    (root / "worlds" / "世界甲.json").write_text(
        json.dumps(
            {
                "entries": {
                    "0": {"uid": 1, "comment": "常驻条目", "content": "常驻正文", "constant": True,
                          "position": 4, "depth": 4},
                    "1": {"uid": 2, "comment": "关键词条目", "content": "触发正文", "key": ["下雨"]},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    (root / "characters" / "示例角色.png").write_bytes(
        make_card_png(
            {
                "name": "示例角色",
                "description": "顶层正文",          # V1 回填路径
                "data": {
                    "name": "示例角色",
                    "description": "",
                    "character_book": {
                        "entries": [{"uid": 9, "comment": "角色内嵌设定", "content": "内嵌正文"}]
                    },
                },
            }
        )
    )

    (root / "chats" / "示例角色" / "会话一.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"chat_metadata": {"create_date": "2026-01-02"}}, ensure_ascii=False),
                json.dumps({"name": "示例角色", "is_user": False, "mes": "你好呀"},
                           ensure_ascii=False),
                json.dumps({"name": "用户", "is_user": True, "mes": "今天很累"},
                           ensure_ascii=False),
                "",  # 空行应被忽略
                json.dumps({"name": "示例角色", "is_user": False, "mes": ""},
                           ensure_ascii=False),  # 空消息跳过
            ]
        ),
        encoding="utf-8",
    )
    return root


def test_data_source_reads_everything(tb, tmp_path, populated_tavern) -> None:
    source = tb.TavernDataSource(_ctx_for(tmp_path, populated_tavern))
    snapshot = source.read()

    # 世界书 2 条 + 角色内嵌书 1 条
    assert len(snapshot.entries) == 3
    assert {e.title for e in snapshot.entries} == {"常驻条目", "关键词条目", "角色内嵌设定"}

    # 角色内嵌条目带归属标注（宿主据此设 scope）
    embedded = next(e for e in snapshot.entries if e.title == "角色内嵌设定")
    assert embedded.extra["character"] == "示例角色"

    assert [c.name for c in snapshot.characters] == ["示例角色"]
    assert snapshot.characters[0].description == "顶层正文"     # V1 回填生效

    assert len(snapshot.sessions) == 1
    session = snapshot.sessions[0]
    assert session.character_name == "示例角色"
    assert session.created_at == "2026-01-02"
    assert [(m.role, m.text) for m in session.messages] == [
        ("assistant", "你好呀"),
        ("user", "今天很累"),
    ]
    assert snapshot.counts == {"entries": 3, "characters": 1, "sessions": 1}


def test_root_detection_accepts_three_levels(tb, tmp_path, populated_tavern) -> None:
    """用户填 ST 根目录 / data 目录 / profile 目录都能识别。"""
    for probe in (populated_tavern, populated_tavern.parent, populated_tavern.parent.parent):
        source = tb.TavernDataSource(_ctx_for(tmp_path, probe))
        assert source.root == populated_tavern, f"未识别：{probe}"


def test_unconfigured_dir_yields_warning_not_error(tb, tmp_path) -> None:
    from app.plugins.capabilities import FilesystemPermission, Permission

    manifest = PluginManifest(
        id="tavern-bridge",
        display_name="酒馆数据接入",
        permissions=Permission(filesystem=FilesystemPermission(read=["${tavern_dir}"])),
    )
    ctx = PluginContext(manifest=manifest, settings={}, data_dir=tmp_path)

    snapshot = tb.TavernDataSource(ctx).read()

    assert snapshot.counts == {"entries": 0, "characters": 0, "sessions": 0}
    assert any("未配置" in w for w in snapshot.warnings)


def test_corrupt_files_record_warnings_instead_of_raising(tb, tmp_path, populated_tavern) -> None:
    """单个文件损坏只记 warning——不因一张坏卡放弃整批接入。"""
    (populated_tavern / "worlds" / "坏书.json").write_text("{不是合法 JSON", encoding="utf-8")
    (populated_tavern / "characters" / "坏卡.png").write_bytes(b"not a png")
    (populated_tavern / "chats" / "示例角色" / "坏会话.jsonl").write_text(
        "不是JSON\n" + json.dumps({"is_user": False, "mes": "有效消息"}, ensure_ascii=False),
        encoding="utf-8",
    )

    snapshot = tb.TavernDataSource(_ctx_for(tmp_path, populated_tavern)).read()

    assert len(snapshot.entries) == 3           # 好数据照常读到
    assert len(snapshot.characters) == 1
    assert len(snapshot.sessions) == 2          # 坏会话里仍解析出有效的那条
    assert len(snapshot.warnings) >= 3
    assert any("世界书读取失败" in w for w in snapshot.warnings)
    assert any("角色卡解析失败" in w for w in snapshot.warnings)
    assert any("会话行损坏" in w for w in snapshot.warnings)


# =============================================================
# 只读强制（核心约束）
# =============================================================


def test_plugin_is_read_only_and_write_is_rejected(tb, tmp_path, populated_tavern) -> None:
    """★ manifest 声明只读 → 即使插件代码想写也拿不到能力（架构约束，非约定）。"""
    ctx = _ctx_for(tmp_path, populated_tavern)

    assert ctx.is_read_only is True
    with pytest.raises(PermissionError, match="只读"):
        ctx.write_text(populated_tavern / "worlds" / "篡改.json", "{}")

    # 反向确认：酒馆数据一个字节都没变
    assert not (populated_tavern / "worlds" / "篡改.json").exists()


def test_plugin_cannot_read_outside_tavern_dir(tb, tmp_path, populated_tavern) -> None:
    outside = tmp_path / "别处的密钥.txt"
    outside.write_text("secret", encoding="utf-8")

    ctx = _ctx_for(tmp_path, populated_tavern)
    with pytest.raises(PermissionError, match="越权"):
        ctx.read_text(outside)


# =============================================================
# 工具与端到端加载
# =============================================================


def test_status_tool_reports_counts(tb, tmp_path, populated_tavern) -> None:
    source = tb.TavernDataSource(_ctx_for(tmp_path, populated_tavern))
    spec = tb.build_status_tool(source.read())

    assert spec.name == "tavern_library_status"
    assert "tavern" in spec.tags

    payload = spec.handler(None, None)
    assert payload["entries"] == 3
    assert payload["characters"] == ["示例角色"]
    assert payload["sessions"] == 1
    assert payload["sessions_in_chars"] == 2


def test_directory_plugin_loads_end_to_end(tmp_path, populated_tavern) -> None:
    """★ 端到端：manifest + 目录 → discover → 配置 → setup → 贡献被收集。

    这条用例同时验证插件体系的「目录插件」通路（动态导入 + build(ctx) 约定）。
    """
    from app.plugins.manager import PluginManager

    plugins_root = _PLUGIN_PATH.parents[1]          # backend/plugins
    registry = PluginRegistry()
    manager = PluginManager(
        registry, data_dir=tmp_path / "data", plugin_dirs=[plugins_root]
    )

    # 1) 发现：只读 manifest，不执行插件代码
    found = manager.discover()
    found_ids = [r.manifest.id for r in found]
    # 不断言「恰好只有它」：plugins/ 是随项目分发的第一方插件目录，
    # 以后会继续长（已有 live2d-model-source），逐个列举会把每次新增都变成测试失败。
    assert "tavern-bridge" in found_ids
    registration = registry.get("tavern-bridge")
    assert registration is not None
    assert registration.manifest.read_only is True
    assert not registration.module_loaded
    assert registration.manifest.enabled is False      # 默认禁用（需先配置目录）

    # 2) 配置 + 启用
    manager.save_settings("tavern-bridge", {"tavern_dir": str(populated_tavern)})
    registry.set_enabled("tavern-bridge", True)

    # 3) setup：动态导入入口、执行 build(ctx)、收集贡献
    ready = manager.setup_all()
    assert "tavern-bridge" in ready

    registration = registry.get("tavern-bridge")
    assert registration.module_loaded is True
    assert registration.state is PluginState.LOADED
    assert len(registration.datasources) == 1
    assert [spec.name for spec in registration.tools] == ["tavern_library_status"]

    # 4) 贡献进入聚合视图，且能被复读（数据源可再次读取）
    #    注意：本用例的注册表里只有 tavern-bridge（未注册内置工具族）
    assert [spec.name for spec in registry.tools()] == ["tavern_library_status"]
    source = registration.datasources[0]
    assert source.read().counts["entries"] == 3

    # 5) 关闭：状态回到 DISCOVERED
    manager.start_all()
    manager.shutdown_all()
    assert registry.get("tavern-bridge").state is PluginState.DISCOVERED


def test_disabled_plugin_is_not_setup(tmp_path, populated_tavern) -> None:
    """默认禁用时不该被 setup（避免未配置就读取用户目录）。"""
    from app.plugins.manager import PluginManager

    registry = PluginRegistry()
    manager = PluginManager(
        registry, data_dir=tmp_path / "data", plugin_dirs=[_PLUGIN_PATH.parents[1]]
    )
    manager.discover()
    manager.setup_all()

    registration = registry.get("tavern-bridge")
    assert registration is not None
    assert registration.module_loaded is False
    assert registration.datasources == []


def test_entry_missing_build_function_marks_failed(tmp_path) -> None:
    """入口缺少 build(ctx) → 标记 FAILED 且不阻断宿主。"""
    from app.plugins.manager import PluginManager

    plugin_dir = tmp_path / "plugins" / "bad-entry"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(
        '{"id": "bad-entry", "display_name": "无入口函数的插件", "enabled": true,'
        ' "entry": "plugin.py"}',
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text("# 故意不定义 build()\n", encoding="utf-8")

    registry = PluginRegistry()
    manager = PluginManager(registry, data_dir=tmp_path / "data", plugin_dirs=[tmp_path / "plugins"])
    manager.discover()
    ready = manager.setup_all()

    assert "bad-entry" not in ready
    registration = registry.get("bad-entry")
    assert registration is not None
    assert registration.state is PluginState.FAILED
    assert "build(ctx)" in registration.error


def test_plugin_settings_persisted_round_trip(tmp_path) -> None:
    """插件配置落 data/plugins/<id>/settings.json，重启后仍在。"""
    from app.plugins.manager import PluginManager

    registry = PluginRegistry()
    manager = PluginManager(registry, data_dir=tmp_path / "data", plugin_dirs=[])
    path = manager.save_settings("tavern-bridge", {"tavern_dir": "/tmp/x"})

    assert path.is_file()
    assert manager._load_settings("tavern-bridge") == {"tavern_dir": "/tmp/x"}

    # 新管理器（模拟重启）仍读得到
    fresh = PluginManager(PluginRegistry(), data_dir=tmp_path / "data", plugin_dirs=[])
    assert fresh._load_settings("tavern-bridge") == {"tavern_dir": "/tmp/x"}


def test_corrupt_settings_ignored(tmp_path) -> None:
    from app.plugins.manager import PluginManager

    manager = PluginManager(PluginRegistry(), data_dir=tmp_path / "data", plugin_dirs=[])
    path = tmp_path / "data" / "plugins" / "tavern-bridge" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{不是 JSON", encoding="utf-8")

    assert manager._load_settings("tavern-bridge") == {}
