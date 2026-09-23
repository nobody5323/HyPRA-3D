"""manifest 模型测试：id 校验、分层判定、只读判定、加载顺序排序。"""

import pytest

from app.plugins.capabilities import (
    CapabilityType,
    FilesystemPermission,
    Permission,
    PluginLayer,
)
from app.plugins.manifest import PluginManifest, load_manifest, sort_manifests


def test_minimal_manifest_defaults() -> None:
    """只给必填项即可构造；其余字段有安全默认值。"""
    manifest = PluginManifest(id="demo", display_name="示例")
    assert manifest.layer is PluginLayer.BUILTIN   # 默认非 core（可禁用）
    assert manifest.enabled is True
    assert manifest.loading_order == 100
    assert manifest.permissions.filesystem.write is False   # 默认只读
    assert manifest.read_only is True
    assert manifest.is_core is False


def test_core_layer_is_marked() -> None:
    manifest = PluginManifest(id="core-x", display_name="核心", layer=PluginLayer.CORE)
    assert manifest.is_core is True
    assert manifest.read_only is True


@pytest.mark.parametrize("bad_id", ["", "Upper", "has space", "a/b", "-leading", "x" * 65])
def test_invalid_ids_rejected(bad_id: str) -> None:
    with pytest.raises(ValueError):
        PluginManifest(id=bad_id, display_name="非法")


@pytest.mark.parametrize("good_id", ["a", "tavern-bridge", "llm.mock_v2", "x9"])
def test_valid_ids_accepted(good_id: str) -> None:
    assert PluginManifest(id=good_id, display_name="合法").id == good_id


def test_strict_readonly_permission() -> None:
    """只读声明：write=False 时不允许携带写路径（由注册表强制，见 test_registry）。"""
    perms = Permission(filesystem=FilesystemPermission(read=["/data/tavern"]))
    assert perms.is_read_only is True

    writable = Permission(
        filesystem=FilesystemPermission(write=True, write_paths=["/data/plugins"])
    )
    assert writable.is_read_only is False


def test_sort_manifests_by_loading_order() -> None:
    """加载顺序：小者先；同序时按 id 稳定排序（与 ST sortManifestsByOrder 同语义）。"""
    items = [
        PluginManifest(id="c", display_name="C", loading_order=50),
        PluginManifest(id="a", display_name="A", loading_order=10),
        PluginManifest(id="b", display_name="B", loading_order=50),
    ]
    assert [m.id for m in sort_manifests(items)] == ["a", "b", "c"]


def test_load_manifest_from_directory(tmp_path) -> None:
    """第三方插件从目录里的 manifest.json 载入。"""
    plugin_dir = tmp_path / "demo-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text(
        """
        {
          "id": "demo-plugin",
          "display_name": "演示插件",
          "layer": "third-party",
          "capabilities": ["datasource", "settings"],
          "permissions": {"filesystem": {"read": ["${data_dir}"], "write": false}}
        }
        """,
        encoding="utf-8",
    )

    manifest = load_manifest(plugin_dir)

    assert manifest.id == "demo-plugin"
    assert manifest.layer is PluginLayer.THIRD_PARTY
    assert manifest.capabilities == [CapabilityType.DATASOURCE, CapabilityType.SETTINGS]
    assert manifest.permissions.filesystem.read == ["${data_dir}"]
    assert manifest.read_only is True


def test_load_manifest_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "not-exist")
