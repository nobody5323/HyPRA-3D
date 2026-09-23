"""PluginContext 测试：权限受控的文件访问（只读插件写入必须被拒）。"""

import pytest

from app.plugins.capabilities import FilesystemPermission, Permission, PluginLayer
from app.plugins.context import PluginContext
from app.plugins.manifest import PluginManifest


def _ctx(
    tmp_path,
    *,
    read: tuple[str, ...] = (),
    write: bool = False,
    write_paths: tuple[str, ...] = (),
    settings: dict | None = None,
) -> PluginContext:
    """构造第三方插件上下文；权限用扁平关键字传入，内部组装成嵌套声明。"""
    manifest = PluginManifest(
        id="demo",
        display_name="演示",
        layer=PluginLayer.THIRD_PARTY,
        permissions=Permission(
            filesystem=FilesystemPermission(
                read=list(read), write=write, write_paths=list(write_paths)
            )
        ),
    )
    return PluginContext(manifest=manifest, settings=settings or {}, data_dir=tmp_path)


# ---------------- 只读插件 ----------------


def test_read_only_plugin_can_read_allowed_path(tmp_path) -> None:
    data = tmp_path / "tavern"
    data.mkdir()
    (data / "world.json").write_text("{}", encoding="utf-8")

    ctx = _ctx(tmp_path, read=(str(data),))
    assert ctx.read_text(data / "world.json") == "{}"
    assert ctx.is_read_only is True


def test_read_only_plugin_cannot_write(tmp_path) -> None:
    """核心约束：声明只读的插件**拿不到写能力**（不是约定，是代码保证）。"""
    data = tmp_path / "tavern"
    data.mkdir()

    ctx = _ctx(tmp_path, read=(str(data),))
    with pytest.raises(PermissionError, match="只读"):
        ctx.write_text(data / "hacked.json", "oops")


def test_read_outside_whitelist_rejected(tmp_path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "secret.txt").write_text("x", encoding="utf-8")

    ctx = _ctx(tmp_path, read=(str(allowed),))
    with pytest.raises(PermissionError, match="越权"):
        ctx.read_text(other / "secret.txt")


def test_no_read_permission_declared(tmp_path) -> None:
    ctx = _ctx(tmp_path)   # 未声明任何读权限
    with pytest.raises(PermissionError, match="未声明读权限"):
        ctx.read_text(tmp_path / "anything.txt")


# ---------------- 可写插件 ----------------


def test_writable_plugin_writes_inside_whitelist(tmp_path) -> None:
    out = tmp_path / "out"
    ctx = _ctx(tmp_path, write=True, write_paths=(str(out),))
    ctx.write_text(out / "nested" / "a.txt", "hello")   # 自动建父目录
    assert (out / "nested" / "a.txt").read_text(encoding="utf-8") == "hello"


def test_writable_plugin_cannot_write_outside_whitelist(tmp_path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    ctx = _ctx(tmp_path, write=True, write_paths=(str(out),))
    with pytest.raises(PermissionError, match="越权"):
        ctx.write_text(elsewhere / "x.txt", "nope")


def test_read_whitelist_does_not_grant_write(tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    ctx = _ctx(tmp_path, read=(str(data),), write=False)
    with pytest.raises(PermissionError):
        ctx.write_text(data / "x.txt", "nope")


# ---------------- 占位符与辅助 ----------------


def test_setting_placeholder_expanded_and_unresolved_skipped(tmp_path) -> None:
    """`${tavern_data_dir}` 由配置值替换；未配置的项被跳过（记 warning 不崩）。"""
    tavern = tmp_path / "tavern"
    tavern.mkdir()
    (tavern / "a.json").write_text("1", encoding="utf-8")

    ctx = _ctx(
        tmp_path,
        read=("${tavern_data_dir}", "${not_configured}"),
        settings={"tavern_data_dir": str(tavern)},
    )

    assert ctx.read_text(tavern / "a.json") == "1"


def test_list_dir_and_read_json(tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "b.json").write_text('{"k": 1}', encoding="utf-8")
    (data / "a.txt").write_text("x", encoding="utf-8")

    ctx = _ctx(tmp_path, read=(str(data),))
    assert [p.name for p in ctx.list_dir(data)] == ["a.txt", "b.json"]
    assert ctx.read_json(data / "b.json") == {"k": 1}


def test_own_data_dir_is_isolated_per_plugin(tmp_path) -> None:
    """插件自有状态目录：无需额外权限声明，路径按插件 id 隔离。"""
    ctx = _ctx(tmp_path)
    own = ctx.own_data_dir()
    assert own == tmp_path / "plugins" / "demo"
    assert own.is_dir()
