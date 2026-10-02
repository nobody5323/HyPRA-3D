"""运行时路径解析测试（app/paths.py 与 config 的路径字段解析）。

为什么值得单独测：打包改造的成败全在「三个基准目录是否解析正确」，
而这**不需要真的打包**就能验证 —— 模拟 `sys.frozen` 与环境变量即可。

三种基准目录的语义不可混用：
- `resource_root()` 只读资源（随程序分发）
- `data_root()` 可写数据
- `config_file()` 用户配置
"""

from pathlib import Path

from app import paths


def _backend_dir() -> Path:
    """源码树里的 backend/（paths.py 位于 backend/app/）。"""
    return Path(paths.__file__).resolve().parent.parent


# ---------- 开发形态（源码树）----------


def test_dev_roots_all_point_to_backend(monkeypatch) -> None:
    """开发形态：资源根与数据根都指向 backend/，与改造前行为一致。"""
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    monkeypatch.delenv(paths.CONFIG_FILE_ENV, raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(paths.sys, "frozen", False, raising=False)

    assert paths.is_frozen() is False
    assert paths.resource_root() == _backend_dir()
    assert paths.data_root() == _backend_dir()
    assert paths.config_file() == _backend_dir() / ".env"


def test_is_frozen_defaults_to_false(monkeypatch) -> None:
    """未打包时 `sys.frozen` 通常根本不存在，不能因此抛异常。"""
    monkeypatch.delattr(paths.sys, "frozen", raising=False)

    assert paths.is_frozen() is False


# ---------- 打包形态（PyInstaller onedir）----------


def test_frozen_splits_resource_and_data_roots(monkeypatch, tmp_path) -> None:
    """打包形态：资源根 = `_MEIPASS`，数据根 = `%APPDATA%/HyPRA`，两者必须分开。"""
    meipass = tmp_path / "_internal"
    appdata = tmp_path / "AppData" / "Roaming"
    meipass.mkdir(parents=True)
    appdata.mkdir(parents=True)

    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    monkeypatch.delenv(paths.CONFIG_FILE_ENV, raising=False)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(meipass), raising=False)

    assert paths.is_frozen() is True
    assert paths.resource_root() == meipass
    # 目录名 "HyPRA" 是与 Electron 的 app.setName("HyPRA") 对齐的硬约定：
    # 改了它两边就会落到不同目录，所以这里写死字面量而不是引用常量
    assert paths.data_root() == appdata / "HyPRA"
    assert paths.config_file() == appdata / "HyPRA" / ".env"


def test_frozen_without_appdata_falls_back_to_home(monkeypatch, tmp_path) -> None:
    """打包形态缺 `%APPDATA%` 时回落到主目录，而不是直接抛异常。"""
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    assert paths.data_root() == tmp_path / "home" / "HyPRA"


def test_frozen_missing_meipass_falls_back_to_exe_dir(monkeypatch, tmp_path) -> None:
    """理论上不该发生，但缺 `_MEIPASS` 时退到 exe 所在目录而不是报错。"""
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    exe = exe_dir / "backend.exe"

    monkeypatch.delattr(paths.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(exe), raising=False)

    assert paths.resource_root() == exe_dir


# ---------- 环境变量覆盖 ----------


def test_data_dir_env_overrides_everything(monkeypatch, tmp_path) -> None:
    """`HYPRA_DATA_DIR` 优先级最高：打包形态下也能把数据改到别处。"""
    target = tmp_path / "custom-data"
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(target))
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert paths.data_root() == target
    # 配置文件默认跟着数据目录走
    assert paths.config_file() == target / ".env"


def test_config_file_env_wins(monkeypatch, tmp_path) -> None:
    """`HYPRA_ENV_FILE` 单独指定配置文件位置。"""
    target = tmp_path / "elsewhere.env"
    monkeypatch.setenv(paths.CONFIG_FILE_ENV, str(target))

    assert paths.config_file() == target


# ---------- 工具函数 ----------


def test_resolve_under_joins_relative_and_keeps_absolute(tmp_path) -> None:
    root = tmp_path / "root"

    assert paths.resolve_under(root, "data/memory.db") == str(root / "data" / "memory.db")
    # 绝对路径原样保留：尊重用户在 .env 里手填的位置
    absolute = tmp_path / "abs.db"
    assert paths.resolve_under(root, str(absolute)) == str(absolute)


def test_path_helpers_compose_under_the_right_root(monkeypatch) -> None:
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(paths.sys, "frozen", False, raising=False)

    assert paths.data_path("data", "memory.db") == paths.data_root() / "data" / "memory.db"
    assert paths.resource_path("app", "worldbook", "entries") == (
        paths.resource_root() / "app" / "worldbook" / "entries"
    )


# ---------- 配置字段解析（消费点真正依赖的行为）----------


def test_settings_resolves_path_fields_to_absolute(monkeypatch) -> None:
    """`Settings` 构造后路径字段应为绝对路径，消费点无需再关心基准目录。"""
    from app.config import Settings

    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)

    settings = Settings(_env_file=None)

    # 可写数据类 → backend/ 下
    assert Path(settings.cold_db_path) == _backend_dir() / "data" / "memory.db"
    assert Path(settings.media_dir) == _backend_dir() / "media"
    assert Path(settings.avatar_models_dir) == _backend_dir() / "data" / "avatar_models"
    # 只读资源类 → 仍在 backend/ 下（随程序分发）
    assert Path(settings.builtin_plugins_dir) == _backend_dir() / "plugins"
    assert Path(settings.skills_dir) == _backend_dir() / "skills"
    # 空串必须保持空串：消费方靠它判「未配置 / 跟随上一层」
    assert settings.session_db_path == ""
    assert settings.mcp_servers_file == ""


def test_settings_follows_data_dir_override(monkeypatch, tmp_path) -> None:
    """`HYPRA_DATA_DIR` 生效时，可写数据字段跟着走（打包形态的核心行为）。"""
    from app.config import Settings

    target = tmp_path / "userdata"
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(target))

    settings = Settings(_env_file=None)

    assert Path(settings.cold_db_path) == target / "data" / "memory.db"
    assert Path(settings.media_dir) == target / "media"
    # 资源类字段**不受**数据目录影响：它们要跟着程序走
    assert Path(settings.skills_dir) == paths.resource_root() / "skills"


def test_settings_keeps_absolute_override_untouched(monkeypatch, tmp_path) -> None:
    """用户在 .env 里填绝对路径时原样保留，不被基准目录改写。"""
    from app.config import Settings

    absolute = tmp_path / "my-memory.db"
    monkeypatch.setenv("COLD_DB_PATH", str(absolute))

    settings = Settings(_env_file=None)

    assert settings.cold_db_path == str(absolute)
