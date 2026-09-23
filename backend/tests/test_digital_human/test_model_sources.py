"""数字人模型来源测试（`AGENTS.md §9.10` 第 15 项）。

要解决的问题：模型「有哪些可以用」原本硬编码在前端常量表里，加一个模型要改前端代码。
这里验证来源可插拔之后的两条通路（本机库 / 清单）与聚合规则。

反复出现的关注点：
1. **一个来源坏掉不能拖垮整张清单**（坏目录、坏 JSON、越权都只记警告）；
2. **合并时已装的胜出，但清单侧元信息要补进来**——作者/授权是用户判断
   「能不能用」的依据（§6 合规红线），本机库里没有这些；
3. **插件路径必须走宿主的受控读 API**，否则 §9.3 的权限声明只是纸面承诺。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.digital_human.model_sources import (
    LocalLibrarySource,
    ModelSourceRegistry,
    RemoteManifestSource,
    build_default_sources,
)

#: 一个能读的最小 AvatarModel 元数据
META = {
    "id": "hiyori",
    "name": "Hiyori",
    "kind": "live2d",
    "createdAt": "2026-01-01T00:00:00",
    "updatedAt": "2026-01-01T00:00:00",
    "entry": "pet.model3.json",
    "expressions": ["smile", "angry"],
    "images": [],
    "expressionMap": {},
    "meta": {},
}


def make_library(root: Path, models: list[dict]) -> Path:
    """在 root 下造一个模型库（每个模型一个目录 + model.json）。

    文件名用 `AvatarModelStore` 的约定（`MODEL_META_FILE`）——写错了会静默读不到，
    而这两种读路径（插件走受控 API / 宿主直接读）都只看这一个文件。
    """
    from app.digital_human.model_store import MODEL_META_FILE

    root.mkdir(parents=True, exist_ok=True)
    for model in models:
        directory = root / model["id"]
        directory.mkdir()
        (directory / MODEL_META_FILE).write_text(
            json.dumps(model, ensure_ascii=False), encoding="utf-8"
        )
    return root


def make_manifest(path: Path, models: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "models": models}, ensure_ascii=False), encoding="utf-8")
    return path


class DenyingReader:
    """模拟「路径不在插件读权限白名单内」的宿主读接口。"""

    def list_dir(self, path):  # noqa: ARG002
        raise PermissionError("目录不在插件读权限内")

    def read_json(self, path):  # noqa: ARG002
        raise PermissionError("文件不在插件读权限内")


class AllowingReader:
    """走真实文件系统的宿主读接口（用于验证受控路径本身可用）。"""

    def list_dir(self, path: Path) -> list[Path]:
        return sorted(Path(path).iterdir())

    def read_json(self, path: Path):
        return json.loads(Path(path).read_text(encoding="utf-8"))


# =============================================================
# 本机模型库来源
# =============================================================


def test_local_library_lists_installed_models(tmp_path: Path) -> None:
    root = make_library(tmp_path / "library", [META])

    descriptors = LocalLibrarySource(root).list_models()

    assert len(descriptors) == 1
    model = descriptors[0]
    assert model.id == "hiyori"
    assert model.installed is True
    assert model.entry == "pet.model3.json"
    assert model.source == "local-library"
    assert "Live2D" in model.description


def test_local_library_reports_image_kind(tmp_path: Path) -> None:
    root = make_library(
        tmp_path / "library", [{**META, "id": "art", "kind": "images", "images": ["a.png", "b.png"]}]
    )

    descriptor = LocalLibrarySource(root).list_models()[0]

    assert descriptor.kind == "images"
    assert "2 张图片" in descriptor.description


def test_local_library_missing_dir_is_empty(tmp_path: Path) -> None:
    assert LocalLibrarySource(tmp_path / "nope").list_models() == []


def test_local_library_unconfigured_is_unavailable() -> None:
    source = LocalLibrarySource("")
    assert source.available() is False
    assert source.list_models() == []


def test_local_library_skips_dirs_without_meta(tmp_path: Path) -> None:
    root = make_library(tmp_path / "library", [META])
    (root / "leftover").mkdir()  # 残留目录，不是模型

    assert [item.id for item in LocalLibrarySource(root).list_models()] == ["hiyori"]


def test_local_library_survives_corrupt_meta(tmp_path: Path) -> None:
    """一个模型的元数据坏掉，不该让整个模型库空掉。"""
    from app.digital_human.model_store import MODEL_META_FILE

    root = make_library(tmp_path / "library", [META])
    broken = root / "broken"
    broken.mkdir()
    (broken / MODEL_META_FILE).write_text("{ 不是 JSON", encoding="utf-8")

    assert [item.id for item in LocalLibrarySource(root).list_models()] == ["hiyori"]


# =============================================================
# 受控读路径（插件路径）
# =============================================================


def test_local_library_reads_via_host_reader(tmp_path: Path) -> None:
    """插件贡献的实例带 reader：走宿主的受控读 API。"""
    root = make_library(tmp_path / "library", [META])

    descriptors = LocalLibrarySource(root, reader=AllowingReader()).list_models()

    assert [item.id for item in descriptors] == ["hiyori"]


def test_local_library_permission_denied_is_not_an_error(tmp_path: Path) -> None:
    """越权只记警告——§9.3 说权限是架构约束，那被拒时插件就该拿不到东西，
    但**不该把整张清单打断**（用户看到的应该是「空」而不是「崩了」）。"""
    root = make_library(tmp_path / "library", [META])

    assert LocalLibrarySource(root, reader=DenyingReader()).list_models() == []


# =============================================================
# 清单来源
# =============================================================


def test_manifest_lists_available_models(tmp_path: Path) -> None:
    path = make_manifest(
        tmp_path / "manifest.json",
        [
            {
                "id": "hiyori",
                "name": "Hiyori",
                "author": "示例作者",
                "license": "示例条款",
                "homepage": "https://example.invalid/x",
                "preview": "https://example.invalid/p.png",
            }
        ],
    )

    descriptor = RemoteManifestSource(str(path)).list_models()[0]

    assert descriptor.id == "hiyori"
    assert descriptor.installed is False
    assert descriptor.author == "示例作者"
    assert descriptor.license == "示例条款"
    assert descriptor.homepage == "https://example.invalid/x"
    assert descriptor.preview_url == "https://example.invalid/p.png"


def test_manifest_accepts_file_url(tmp_path: Path) -> None:
    path = make_manifest(tmp_path / "manifest.json", [{"id": "a", "name": "甲"}])

    assert RemoteManifestSource(path.as_uri()).list_models()[0].id == "a"


def test_manifest_file_url_survives_spaces_and_chinese(tmp_path: Path) -> None:
    """file:// 里的空格 / 中文是百分号编码的，必须解码。

    回归点：`Path.as_uri()` 会把「Talk and heart」编成 `Talk%20and%20heart`，
    而 `Path(...)` 不会自动解码——路径带空格的用户（Windows 上很常见）会静默读不到。
    """
    spaced = tmp_path / "我的 模型 目录"
    path = make_manifest(spaced / "manifest.json", [{"id": "a", "name": "甲"}])

    assert "%20" in path.as_uri() or "%" in path.as_uri()  # 确实编了码
    assert RemoteManifestSource(path.as_uri()).list_models()[0].id == "a"


def test_manifest_rejects_network_location(tmp_path: Path) -> None:
    """只支持本地路径：拉远程 JSON 是网络访问，而插件权限模型对网络没有闸门——
    声明 `network.hosts: []` 却真去发请求，等于把声明写成空话。"""
    source = RemoteManifestSource("https://example.invalid/manifest.json")

    assert source.available() is True     # 配置了
    assert source.list_models() == []     # 但读不到（并记警告）


def test_manifest_skips_entries_without_id(tmp_path: Path) -> None:
    path = make_manifest(tmp_path / "manifest.json", [{"name": "无 id"}, {"id": "ok", "name": "有 id"}])

    assert [item.id for item in RemoteManifestSource(str(path)).list_models()] == ["ok"]


def test_manifest_unknown_kind_falls_back_to_live2d(tmp_path: Path) -> None:
    path = make_manifest(tmp_path / "manifest.json", [{"id": "x", "kind": "??? "}])

    assert RemoteManifestSource(str(path)).list_models()[0].kind == "live2d"


def test_manifest_passes_through_unknown_fields(tmp_path: Path) -> None:
    """来源特有字段透传，宿主不解释（§9.4 的中性契约精神）。"""
    path = make_manifest(tmp_path / "manifest.json", [{"id": "x", "custom": {"tag": "v"}}])

    assert RemoteManifestSource(str(path)).list_models()[0].extra == {"custom": {"tag": "v"}}


def test_manifest_corrupt_json_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{ 不是 JSON", encoding="utf-8")

    assert RemoteManifestSource(str(path)).list_models() == []


def test_manifest_without_models_array_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"version": 1}', encoding="utf-8")

    assert RemoteManifestSource(str(path)).list_models() == []


def test_manifest_permission_denied_is_not_an_error(tmp_path: Path) -> None:
    path = make_manifest(tmp_path / "manifest.json", [{"id": "a"}])

    assert RemoteManifestSource(str(path), reader=DenyingReader()).list_models() == []


# =============================================================
# 聚合
# =============================================================


def test_registry_merges_installed_and_available(tmp_path: Path) -> None:
    library = make_library(tmp_path / "library", [META])
    manifest = make_manifest(
        tmp_path / "manifest.json",
        [
            {"id": "hiyori", "name": "Hiyori（清单）", "author": "作者", "license": "条款"},
            {"id": "new-one", "name": "还没装的"},
        ],
    )

    models = ModelSourceRegistry(
        build_default_sources(models_dir=library, manifest_path=str(manifest))
    ).descriptors()

    by_id = {model.id: model for model in models}
    assert set(by_id) == {"hiyori", "new-one"}
    # 已装的在前
    assert [model.id for model in models] == ["hiyori", "new-one"]
    # 已装的版本胜出，但清单侧的授权信息被补了进来——本机库里没有这些，
    # 而它们正是用户判断「能不能用」的依据
    assert by_id["hiyori"].installed is True
    assert by_id["hiyori"].display_name == "Hiyori"
    assert by_id["hiyori"].author == "作者"
    assert by_id["hiyori"].license == "条款"
    assert by_id["new-one"].installed is False


def test_registry_skips_unavailable_sources(tmp_path: Path) -> None:
    library = make_library(tmp_path / "library", [META])

    models = ModelSourceRegistry(
        build_default_sources(models_dir=library, manifest_path="")
    ).descriptors()

    assert [model.id for model in models] == ["hiyori"]


def test_registry_status_reports_each_source(tmp_path: Path) -> None:
    library = make_library(tmp_path / "library", [META])

    status = ModelSourceRegistry(
        build_default_sources(models_dir=library, manifest_path="")
    ).status()

    by_id = {item["id"]: item for item in status}
    assert by_id["local-library"]["available"] is True
    assert by_id["local-library"]["count"] == 1
    assert by_id["remote-manifest"]["available"] is False
    assert by_id["remote-manifest"]["count"] == 0


def test_registry_without_sources_is_empty() -> None:
    registry = ModelSourceRegistry()
    assert registry.descriptors() == []
    assert registry.status() == []


# =============================================================
# 插件形态
# =============================================================


def test_plugin_contributes_registry(tmp_path: Path) -> None:
    """插件入口产出 `ModelSourceRegistry` 作为 datasource 贡献。"""
    import importlib.util
    import sys

    from app.plugins.context import PluginContext
    from app.plugins.manifest import load_manifest

    plugin_dir = Path(__file__).parents[2] / "plugins" / "live2d-model-source"
    spec = importlib.util.spec_from_file_location(
        "hypra_test_model_source_plugin", plugin_dir / "plugin.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    library = make_library(tmp_path / "data" / "avatar_models", [META])
    manifest = make_manifest(tmp_path / "manifest.json", [{"id": "extra", "name": "外加"}])

    # 用**真实 manifest**：读权限白名单就写在里面，手搓一个空的会把插件挡在门外
    ctx = PluginContext(
        manifest=load_manifest(plugin_dir),
        settings={"manifest_path": str(manifest)},
        data_dir=tmp_path / "data",
    )
    contributions = module.build(ctx)

    registry = contributions["datasources"][0]
    assert isinstance(registry, ModelSourceRegistry)
    assert {model.id for model in registry.descriptors()} == {"hiyori", "extra"}
    assert library.is_dir()  # 走的是默认子目录 avatar_models


def test_plugin_manifest_is_read_only(tmp_path: Path) -> None:
    """零写操作是硬约束：manifest 声明 write=false，宿主因此不授予写句柄。"""
    from app.plugins.manifest import load_manifest

    manifest = load_manifest(
        Path(__file__).parents[2] / "plugins" / "live2d-model-source"
    )

    assert manifest.read_only is True
    assert manifest.permissions.network.hosts == []
    assert {"datasource", "settings"} <= {item.value for item in manifest.capabilities}
