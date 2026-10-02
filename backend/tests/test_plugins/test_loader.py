"""插件导入器单元测试（`app/plugins/loader.py`）。

API 层的端到端用例在 `test_api.py`；这里只钉住**搬运过程本身**的不变量——
它们出错时症状很隐蔽（用户原来的插件被弄丢、或留下半个目录让下次启动 FAILED）：
写入失败要能回滚、源目录就在目标位置时不要自己拷自己、构建缓存不要跟着搬。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.plugins.loader import PluginImportError, import_plugin_dir

_ENTRY = '"""示例入口。"""\n\n\ndef build(ctx):\n    return {}\n'


def _make_source(root: Path, plugin_id: str, *, entry_body: str = _ENTRY, **overrides) -> Path:
    directory = root / plugin_id
    directory.mkdir(parents=True)
    manifest = {
        "id": plugin_id,
        "display_name": "示例插件",
        "layer": "third-party",
        "entry": "plugin.py",
        "enabled": True,
    }
    manifest.update(overrides)
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (directory / "plugin.py").write_text(entry_body, encoding="utf-8")
    return directory


def test_import_rejects_missing_source(tmp_path: Path) -> None:
    with pytest.raises(PluginImportError, match="不存在"):
        import_plugin_dir(tmp_path / "nope", tmp_path / "plugins")


def test_import_rejects_core_and_builtin_layers(tmp_path: Path) -> None:
    """只有 third-party 能被导入：否则「核心不可禁用」可以被一次导入绕过（§9.2）。"""
    for layer in ("core", "builtin"):
        source = _make_source(tmp_path / f"src-{layer}", "demo", layer=layer)
        with pytest.raises(PluginImportError, match="third-party"):
            import_plugin_dir(source, tmp_path / "plugins")


def test_import_rejects_id_taken_by_builtin(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "src", "demo")

    with pytest.raises(PluginImportError, match="占用"):
        import_plugin_dir(source, tmp_path / "plugins", known_ids={"demo"})


def test_import_source_inside_target_is_noop(tmp_path: Path) -> None:
    """用户直接指向 `data/plugins/<id>` 时不该自己拷自己（会无限递归 / 破坏内容）。"""
    plugins_dir = tmp_path / "plugins"
    source = _make_source(plugins_dir, "demo")

    manifest = import_plugin_dir(source, plugins_dir)

    assert manifest.id == "demo"
    assert (source / "plugin.py").read_text("utf-8") == _ENTRY


def test_import_restores_previous_plugin_when_readback_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`replace=True` 时若回读失败，必须把**用户原来那份**放回去（不能两边都没有）。"""
    import app.plugins.loader as loader

    plugins_dir = tmp_path / "plugins"
    target = _make_source(plugins_dir, "demo", entry_body='"""旧的。"""\n')
    source = _make_source(tmp_path / "src", "demo", entry_body='"""新的。"""\n')

    real_load = loader.load_manifest
    calls = {"count": 0}

    def flaky(path):
        calls["count"] += 1
        if calls["count"] >= 2:  # 第一次是写前校验，第二次是落盘后的回读
            raise ValueError("磁盘出问题了")
        return real_load(path)

    monkeypatch.setattr(loader, "load_manifest", flaky)

    with pytest.raises(PluginImportError, match="写入失败"):
        import_plugin_dir(source, plugins_dir, replace=True)

    assert (target / "plugin.py").read_text("utf-8") == '"""旧的。"""\n'
    # 暂存 / 备份目录都不该留下
    leftovers = [item.name for item in plugins_dir.iterdir() if item.name.startswith(".")]
    assert leftovers == []


def test_import_leaves_no_partial_directory_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首次导入失败时不能留下半个目录（否则下次启动会被标成 FAILED 且只能手工删）。"""
    import app.plugins.loader as loader

    plugins_dir = tmp_path / "plugins"
    source = _make_source(tmp_path / "src", "demo")

    monkeypatch.setattr(
        loader, "load_manifest", lambda *_: (_ for _ in ()).throw(ValueError("坏 manifest"))
    )

    with pytest.raises(PluginImportError):
        import_plugin_dir(source, plugins_dir)

    assert not (plugins_dir / "demo").exists()
    assert not plugins_dir.exists() or list(plugins_dir.iterdir()) == []
