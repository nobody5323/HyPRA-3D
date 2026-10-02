"""插件管理 API 测试：列表、启停、配置读写、酒馆记忆导入。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.plugins.capabilities import PluginState
from app.plugins.registry import PluginRegistry, get_registry


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# =============================================================
# 列表
# =============================================================


def test_list_plugins_includes_builtin(client: TestClient) -> None:
    body = client.get("/plugins").json()

    ids = [plugin["id"] for plugin in body["plugins"]]
    assert "llm-providers" in ids
    assert "tools-builtin" in ids
    assert body["summary"]["total"] == len(body["plugins"])
    assert "llm" in body["capabilities"]


def test_list_plugins_exposes_capability_index(client: TestClient) -> None:
    body = client.get("/plugins").json()
    capabilities = body["capabilities"]

    # 每个内置能力族都应出现在索引里，且指向具体插件
    for capability in ("llm", "digital_human", "warm_store", "knowledge_store", "session_store"):
        assert capability in capabilities
        assert capabilities[capability]


# =============================================================
# 启停
# =============================================================


def test_toggle_builtin_plugin(client: TestClient, tmp_path, monkeypatch) -> None:
    from app.api import plugins as plugins_api

    # 启用状态会落盘：把数据目录指到 tmp_path，避免污染真实的 backend/data
    monkeypatch.setattr(plugins_api.get_plugin_manager(), "data_dir", tmp_path)

    response = client.post("/plugins/tools-builtin/enabled", json={"enabled": False})
    assert response.status_code == 200

    body = response.json()
    assert body["enabled"] is False
    tools_plugin = next(p for p in body["plugins"] if p["id"] == "tools-builtin")
    assert tools_plugin["enabled"] is False
    assert tools_plugin["state"] == "disabled"

    # 还原，避免影响其它用例
    client.post("/plugins/tools-builtin/enabled", json={"enabled": True})


def test_toggle_persists_state_file(client: TestClient, tmp_path, monkeypatch) -> None:
    """启停落盘：重启后保持，不会回弹到 manifest 默认值。"""
    from app.api import plugins as plugins_api

    monkeypatch.setattr(plugins_api.get_plugin_manager(), "data_dir", tmp_path)

    client.post("/plugins/tools-builtin/enabled", json={"enabled": False})
    try:
        state_path = tmp_path / "plugins" / "tools-builtin" / "state.json"
        assert state_path.is_file()
        assert json.loads(state_path.read_text(encoding="utf-8")) == {"enabled": False}
    finally:
        client.post("/plugins/tools-builtin/enabled", json={"enabled": True})


def test_put_settings_keeps_plugin_started(client: TestClient, tmp_path, monkeypatch) -> None:
    """保存配置后插件仍为 STARTED——只 setup 不 start 会让界面从「运行中」退成「已加载」。"""
    from app.api import plugins as plugins_api

    monkeypatch.setattr(plugins_api.get_plugin_manager(), "data_dir", tmp_path)

    body = client.put("/plugins/tools-builtin/settings", json={"values": {}}).json()
    tools_plugin = next(p for p in body["plugins"] if p["id"] == "tools-builtin")
    assert tools_plugin["state"] == "started"
    assert tools_plugin["enabled"] is True


def test_toggle_unknown_plugin_404(client: TestClient) -> None:
    response = client.post("/plugins/not-a-plugin/enabled", json={"enabled": True})
    assert response.status_code == 404
    assert "插件不存在" in response.json()["detail"]


def test_core_plugin_cannot_be_disabled_400(client: TestClient) -> None:
    """core 层拒绝禁用——用真实存在的 core 插件验证（若无则跳过）。"""
    registry = get_registry()
    core_ids = [r.manifest.id for r in registry.all() if r.manifest.is_core]
    if not core_ids:  # 当前收编的 7 项均为 builtin，core 尚未落地
        pytest.skip("尚未注册 core 层插件")

    response = client.post(f"/plugins/{core_ids[0]}/enabled", json={"enabled": False})
    assert response.status_code == 400
    assert "core" in response.json()["detail"]


# =============================================================
# 配置
# =============================================================


def test_get_then_put_plugin_settings(client: TestClient, tmp_path, monkeypatch) -> None:
    """配置读写闭环：写进去的能读回来。"""
    from app.api import plugins as plugins_api

    manager = plugins_api.get_plugin_manager()
    monkeypatch.setattr(manager, "data_dir", tmp_path)

    schema_response = client.get("/plugins/tavern-bridge/settings")
    if schema_response.status_code == 404:
        pytest.skip("tavern-bridge 未安装（plugins/ 目录缺失）")

    body = schema_response.json()
    assert "schema" in body
    assert body["permissions"]["filesystem"]["write"] is False   # 只读声明随配置一起暴露

    put_response = client.put(
        "/plugins/tavern-bridge/settings",
        json={"values": {"tavern_dir": str(tmp_path / "fake-tavern")}},
    )
    assert put_response.status_code == 200

    refreshed = client.get("/plugins/tavern-bridge/settings").json()
    assert refreshed["values"]["tavern_dir"] == str(tmp_path / "fake-tavern")


def test_settings_for_unknown_plugin_404(client: TestClient) -> None:
    assert client.get("/plugins/nope/settings").status_code == 404
    assert client.put("/plugins/nope/settings", json={"values": {}}).status_code == 404


# =============================================================
# 声明式配置 → 运行时生效（`AGENTS.md §9.5`）
# =============================================================


@pytest.fixture
def isolated_manager(tmp_path, monkeypatch):
    """把插件可写目录指到 tmp_path，并清掉进程内缓存的配置与启用状态。

    插件管理器是**全局单例**，`_settings` 会跨用例残留——不清的话本机/前一个用例
    存过的插件配置会在 reload_all 时被重新应用。conftest 已负责清覆盖层，
    这里负责清管理器自己的内存。

    启用状态（`registry._enabled_overrides`）同样必须清：它在单例导入时就按
    **开发者本机**的 `data/plugins/<id>/state.json` 填好了（如本机关掉了 tts），
    而把 `data_dir` 指向 tmp_path 只影响之后的读写、改不了已经加载的状态。
    不清的话，用例会依赖「本机有没有关过某个插件」——本机一关就红，
    而且报错信息完全指不到原因。
    """
    from app.api import plugins as plugins_api

    manager = plugins_api.get_plugin_manager()
    monkeypatch.setattr(manager, "data_dir", tmp_path)
    monkeypatch.setattr(manager, "_settings", {})
    monkeypatch.setattr(manager.registry, "_enabled_overrides", {})
    return manager


def test_embedding_settings_hide_secret_and_show_effective(
    client: TestClient, isolated_manager, monkeypatch
) -> None:
    """表单初始值 = 当前生效值，且**密钥字段不回明文**（口径同 /llm/config）。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "siliconflow")
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    monkeypatch.setenv("EMBEDDING_API_KEY", "sk-from-env")

    body = client.get("/plugins/embedding/settings").json()

    assert body["values"]["embedding_provider"] == "siliconflow"
    assert body["values"]["embedding_model"] == "BAAI/bge-m3"
    assert "embedding_api_key" not in body["values"]
    assert "sk-from-env" not in json.dumps(body, ensure_ascii=False)
    assert body["secrets_set"] == ["embedding_api_key"]


def test_put_embedding_settings_applies_without_restart(
    client: TestClient, isolated_manager, monkeypatch
) -> None:
    """保存即生效：不必改 .env、不必重启后端。"""
    from app.config import get_settings

    monkeypatch.setenv("EMBEDDING_PROVIDER", "deterministic")
    response = client.put(
        "/plugins/embedding/settings",
        json={"values": {"embedding_provider": "dashscope", "embedding_model": "text-embedding-v3"}},
    )
    assert response.status_code == 200

    settings = get_settings()
    assert settings.embedding_provider == "dashscope"
    assert settings.embedding_model == "text-embedding-v3"


def test_put_secret_keeps_saved_value(
    client: TestClient, isolated_manager, monkeypatch
) -> None:
    """密码框不回填 → 「没提交」等于「沿用」，而不是把 key 抹掉；响应也不含明文。"""
    import json as json_module

    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    client.put("/plugins/embedding/settings", json={"values": {"embedding_api_key": "sk-first"}})

    # 第二次保存只改模型名（密钥字段压根不提交）
    response = client.put(
        "/plugins/embedding/settings",
        json={"values": {"embedding_model": "text-embedding-v3"}},
    )
    assert "sk-first" not in json_module.dumps(response.json(), ensure_ascii=False)

    saved = json_module.loads(
        (isolated_manager.data_dir / "plugins" / "embedding" / "settings.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["embedding_api_key"] == "sk-first"


def test_put_null_clears_saved_secret(client: TestClient, isolated_manager, monkeypatch) -> None:
    """显式 null = 清除：删掉该键后回落到 .env（用户得有退路）。"""
    import json as json_module

    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("EMBEDDING_API_KEY", "sk-from-env")
    client.put("/plugins/embedding/settings", json={"values": {"embedding_api_key": "sk-ui"}})

    response = client.put(
        "/plugins/embedding/settings", json={"values": {"embedding_api_key": None}}
    )

    saved = json_module.loads(
        (isolated_manager.data_dir / "plugins" / "embedding" / "settings.json").read_text(
            encoding="utf-8"
        )
    )
    assert "embedding_api_key" not in saved
    # 清除后 .env 的 key 重新成为生效值（所以仍显示「已配置」）
    assert response.json()["secrets_set"] == ["embedding_api_key"]


def test_put_settings_rebuilds_consumers_only_when_changed(client: TestClient, isolated_manager) -> None:
    """只有真正受影响的消费方单例才重建。

    三件事都很实际：改一个无运行时配置的插件不该白重建 embedding；
    改 TTS 音色也不该把记忆门面与世界书向量索引（要重编码）一起拖下水；
    反过来，改 embedding 时必须**连知识库一起换**——它也在构造时快照了 provider。
    """
    from app.api import chat as chat_module
    from app.api import knowledge as knowledge_module
    from app.api import media as media_module
    from app.digital_human.local_provider import LocalDigitalHumanProvider
    from app.memory.knowledge.inmemory_store import InMemoryKnowledgeStore
    from app.memory.warm.embedding import DeterministicEmbeddingProvider

    embedding = DeterministicEmbeddingProvider()
    digital_human = LocalDigitalHumanProvider()
    knowledge = InMemoryKnowledgeStore()
    chat_module.set_embedding_provider(embedding)
    media_module.set_digital_human_provider(digital_human)
    knowledge_module.set_knowledge_store(knowledge)

    # tools-builtin 没有与宿主同名的配置键 → 覆盖层无变化 → 谁都不重建
    client.put("/plugins/tools-builtin/settings", json={"values": {}})
    assert chat_module.get_embedding_provider() is embedding
    assert media_module.get_digital_human_provider() is digital_human
    assert knowledge_module.get_knowledge_store() is knowledge

    # TTS 配置 → 只重建数字人驱动；embedding 与知识库保持原样
    client.put(
        "/plugins/tts/settings",
        json={"values": {"gpt_sovits_base_url": "http://127.0.0.1:9999"}},
    )
    assert chat_module.get_embedding_provider() is embedding
    assert knowledge_module.get_knowledge_store() is knowledge
    assert media_module.get_digital_human_provider() is not digital_human

    # embedding 配置 → embedding 与知识库**一起**重建：两者必须同一向量空间
    client.put("/plugins/embedding/settings", json={"values": {"embedding_model": "changed"}})
    assert chat_module.get_embedding_provider() is not embedding
    assert knowledge_module.get_knowledge_store() is not knowledge


def test_disabling_plugin_drops_its_overrides(client: TestClient, isolated_manager, monkeypatch) -> None:
    """禁用插件 = 它的声明式配置不再生效（不能留下幽灵覆盖）。"""
    from app.config import get_settings, runtime_overrides

    monkeypatch.setenv("EMBEDDING_PROVIDER", "deterministic")
    client.put("/plugins/embedding/settings", json={"values": {"embedding_provider": "dashscope"}})
    assert get_settings().embedding_provider == "dashscope"

    client.post("/plugins/embedding/enabled", json={"enabled": False})
    try:
        assert "embedding_provider" not in runtime_overrides()
        assert get_settings().embedding_provider == "deterministic"
    finally:
        client.post("/plugins/embedding/enabled", json={"enabled": True})


# =============================================================
# 酒馆接入（未启用时给出可读错误）
# =============================================================


def test_tavern_status_reports_not_ready_or_data(client: TestClient) -> None:
    """插件未启用 → 409 且提示怎么做；已启用 → 返回读取统计。"""
    response = client.get("/plugins/tavern-bridge/status")

    if response.status_code == 409:
        assert "启用" in response.json()["detail"]
    else:
        assert response.status_code == 200
        body = response.json()
        assert "available" in body
        assert set(body["available"]) == {"entries", "characters", "sessions"}
        # 「待同步多少轮」是界面提示「有新的」的依据；它必须总是存在（没导过就是 0）
        assert isinstance(body["pending_turns"], int)
        assert isinstance(body["pending_sessions"], int)
        assert body["pending_turns"] >= 0 and body["pending_sessions"] >= 0


def test_tavern_import_requires_companion_id(client: TestClient) -> None:
    """缺少 companion_id → 422（参数校验），不会误写进空命名空间。"""
    assert client.post("/plugins/tavern-bridge/import").status_code == 422


# =============================================================
# 接入写在别处的插件（AGENTS.md §9.3）
# =============================================================
#
# 插件由**用户自己写**，宿主只做「校验 + 搬运」：没有 AI 生成、没有代码入参。
# 两条路——导入（本节的 `/plugins/import`）与零拷贝（`PLUGIN_EXTRA_DIRS` + `/plugins/reload`）。

_PLUGIN_CODE = '''"""喝水记录插件：记录每天的杯数。"""

from __future__ import annotations


def build(ctx) -> dict:
    """插件入口：返回本插件对宿主的贡献。"""
    ctx.logger.info("喝水记录已就绪")
    return {"tools": []}
'''


def _dir_entries(path: Path) -> list[str]:
    """目录内容；**目录不存在也算空**——导入被拒时目录本来就还没建。"""
    return sorted(item.name for item in path.iterdir()) if path.exists() else []


def _write_plugin_source(root: Path, plugin_id: str, **manifest_overrides) -> Path:
    """在 `root/<id>/` 造一份「用户手写的插件」，返回该目录。

    每个用例请用**不同的 id**：注册表是进程级单例、且只增不删，成功导入过的 id
    会一直占着位置（`/plugins/import` 把「仍占着位置」当冲突），复用同一个 id
    会让后面的用例被前一个用例的登记拦住。
    """
    directory = root / plugin_id
    directory.mkdir(parents=True)
    manifest = {
        "id": plugin_id,
        "display_name": "喝水记录",
        "version": "0.1.0",
        "layer": "third-party",
        "category": "tool",
        "capabilities": ["tool"],
        "entry": "plugin.py",
    }
    manifest.update(manifest_overrides)
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (directory / "plugin.py").write_text(_PLUGIN_CODE, encoding="utf-8")
    return directory


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch, isolated_manager):
    """把**导入目录**也指到 tmp_path（导入写的是 `PLUGINS_DIR`，与 manager.data_dir 是两回事）。"""
    target = tmp_path / "plugins"
    monkeypatch.setenv("PLUGINS_DIR", str(target))
    monkeypatch.setattr(isolated_manager, "plugin_dirs", [target])
    return target


def test_import_plugin_copies_and_discovers(client: TestClient, plugin_dir, tmp_path) -> None:
    """导入：复制进用户目录、能被重扫发现、原始目录保留。"""
    source = _write_plugin_source(tmp_path / "elsewhere", "copy-check")

    response = client.post("/plugins/import", json={"path": str(source)})

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "copy-check"
    assert (plugin_dir / "copy-check" / "plugin.py").is_file()

    # manifest 原样搬过去（导入不改写用户自己写的内容）
    saved = json.loads((plugin_dir / "copy-check" / "manifest.json").read_text("utf-8"))
    assert saved["layer"] == "third-party"
    assert saved["entry"] == "plugin.py"

    # 源目录保留：用户继续在自己那边开发，宿主不碰
    assert (source / "plugin.py").is_file()

    assert "copy-check" in [item["id"] for item in body["plugins"]]


def test_import_plugin_rejects_builtin_layer(client: TestClient, plugin_dir, tmp_path) -> None:
    """`core` / `builtin` 只能随项目分发——否则「核心不可禁用」可以被一次导入绕过。"""
    source = _write_plugin_source(tmp_path / "elsewhere", "fake-core", layer="core")

    response = client.post("/plugins/import", json={"path": str(source)})

    assert response.status_code == 400
    assert "third-party" in response.json()["detail"]
    assert _dir_entries(plugin_dir) == []


def test_import_plugin_rejects_missing_manifest(client: TestClient, plugin_dir, tmp_path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    response = client.post("/plugins/import", json={"path": str(empty)})

    assert response.status_code == 400
    assert "manifest.json" in response.json()["detail"]
    assert _dir_entries(plugin_dir) == []


def test_import_plugin_rejects_syntax_error(client: TestClient, plugin_dir, tmp_path) -> None:
    """入口文件语法错误 → 400 且**什么都不写**（不留半成品让下次启动 FAILED）。"""
    source = _write_plugin_source(tmp_path / "elsewhere", "broken-syntax")
    (source / "plugin.py").write_text("def build(ctx)\n    return {}\n", encoding="utf-8")

    response = client.post("/plugins/import", json={"path": str(source)})

    assert response.status_code == 400
    assert "语法错误" in response.json()["detail"]
    assert _dir_entries(plugin_dir) == []


def test_import_plugin_rejects_missing_entry(client: TestClient, plugin_dir, tmp_path) -> None:
    source = _write_plugin_source(tmp_path / "elsewhere", "no-entry")
    (source / "plugin.py").unlink()

    response = client.post("/plugins/import", json={"path": str(source)})

    assert response.status_code == 400
    assert "入口文件不存在" in response.json()["detail"]


def test_import_plugin_rejects_contradictory_permissions(
    client: TestClient, plugin_dir, tmp_path
) -> None:
    """只读却声明写路径 → 400 且什么都不写。

    不能靠发现阶段拦：那时文件已落盘，后果是 500 + 后端下次启动报错（只能手工删目录）。
    """
    source = _write_plugin_source(
        tmp_path / "elsewhere",
        "bad-perm-id",
        permissions={"filesystem": {"read": [], "write": False, "write_paths": ["/tmp/x"]}},
    )

    response = client.post("/plugins/import", json={"path": str(source)})

    assert response.status_code == 400
    assert "权限声明不合法" in response.json()["detail"]
    assert _dir_entries(plugin_dir) == []


def test_import_plugin_rejects_builtin_id(client: TestClient, plugin_dir, tmp_path) -> None:
    """与内置插件同名 → 拒收（否则能力中心会出现两个同名项，而新装的那个永远不生效）。"""
    source = _write_plugin_source(tmp_path / "elsewhere", "tavern-bridge")

    response = client.post("/plugins/import", json={"path": str(source)})

    assert response.status_code == 400
    assert "占用" in response.json()["detail"]


def test_import_plugin_rejects_existing_target_without_replace(
    client: TestClient, plugin_dir, tmp_path
) -> None:
    """目标已存在时默认拒绝——不覆盖用户已装好的插件；显式 `replace` 才整体替换。"""
    source = _write_plugin_source(tmp_path / "elsewhere", "replace-check")
    assert client.post("/plugins/import", json={"path": str(source)}).status_code == 200

    again = client.post("/plugins/import", json={"path": str(source)})
    assert again.status_code == 400
    assert "已存在" in again.json()["detail"]

    replaced = client.post(
        "/plugins/import", json={"path": str(source), "replace": True}
    )
    assert replaced.status_code == 200


def test_import_plugin_allows_reimport_after_manual_delete(
    client: TestClient, plugin_dir, isolated_manager, tmp_path
) -> None:
    """手动删掉插件目录后可以重新导入同一个 id（注册表的登记是只增不删的，不能把它当永远占用）。"""
    # 用独有 id：全局注册表在同一个进程里跨用例共享，撞上其它用例装过的 id 会干扰断言
    source = _write_plugin_source(tmp_path / "elsewhere", "reimport-check")
    payload = {"path": str(source)}
    assert client.post("/plugins/import", json=payload).status_code == 200

    import shutil

    shutil.rmtree(plugin_dir / "reimport-check")

    assert client.post("/plugins/import", json=payload).status_code == 200


def test_import_plugin_skips_build_artifacts(
    client: TestClient, plugin_dir, tmp_path
) -> None:
    """`__pycache__` / `.git` 之类不搬过去（拷过去只会污染插件目录）。"""
    source = _write_plugin_source(tmp_path / "elsewhere", "clean-copy")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "plugin.cpython-313.pyc").write_bytes(b"junk")

    assert client.post("/plugins/import", json={"path": str(source)}).status_code == 200

    assert not (plugin_dir / "clean-copy" / "__pycache__").exists()
    assert (plugin_dir / "clean-copy" / "plugin.py").is_file()


def test_reload_discovers_hand_written_plugin(client: TestClient, plugin_dir) -> None:
    """手写的插件丢进目录后，调 `/reload` 就能在能力中心看到（免重启）。"""
    _write_plugin_source(plugin_dir, "hand-made", capabilities=["settings"])

    body = client.post("/plugins/reload").json()

    assert "hand-made" in body["discovered"]
    assert "hand-made" in [item["id"] for item in body["plugins"]]


# =============================================================
# 插件工具 → 对话工具注册表（`AGENTS.md §9.4` 的 `tool` 能力面）
# =============================================================

#: 贡献单个工具的测试插件；manifest 默认禁用，启停由用例控制
_TOOL_PLUGIN_CODE = '''"""测试用插件：贡献一个什么都不做的工具。"""

from __future__ import annotations

from pydantic import BaseModel

from app.tools.registry import ToolSpec


class NoArgs(BaseModel):
    """无参数。"""


def _handler(args, context) -> dict:
    return {"message": "探针工具已执行"}


def build(ctx) -> dict:
    ctx.logger.info("探针工具插件已就绪")
    return {
        "tools": [
            ToolSpec(
                name="probe_tool",
                description="测试用工具",
                parameters=NoArgs.model_json_schema(),
                args_model=NoArgs,
                handler=_handler,
            )
        ]
    }
'''


@pytest.fixture
def make_tool_plugin(client: TestClient, plugin_dir):
    """工厂：造一个「贡献 1 个工具」的插件并重扫发现它。

    每个用例必须用**不同的 id**：注册表是进程级单例，而 `discover()` 对已注册的 id
    直接跳过（「内置优先」），复用同一个 id 会指向上一个用例已删掉的临时目录。
    """

    def _make(plugin_id: str) -> str:
        directory = plugin_dir / plugin_id
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "id": plugin_id,
                    "display_name": "探针工具",
                    "layer": "third-party",
                    "capabilities": ["tool"],
                    "entry": "plugin.py",
                    "enabled": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (directory / "plugin.py").write_text(_TOOL_PLUGIN_CODE, encoding="utf-8")
        client.post("/plugins/reload")
        return plugin_id

    return _make


def _chat_tool_names() -> list[str]:
    """走**真实构建路径**取对话工具注册表的工具名。"""
    from app.api.chat import _build_tool_registry

    return _build_tool_registry().names()


def test_plugin_tools_reach_the_chat_registry(client: TestClient, make_tool_plugin) -> None:
    """**回归**：插件贡献的工具必须真的进对话工具注册表。

    实测过的病：`ToolSpec` 只躺在 `PluginRegistry` 的聚合视图里，而
    `_build_tool_registry()` 只合「内置 + MCP」——工具「声明了却调不到」。
    酒馆接入的 `tavern_library_status` 就是这么消失的：模型永远看不到它。
    """
    plugin_id = make_tool_plugin("probe-tools-a")
    client.post(f"/plugins/{plugin_id}/enabled", json={"enabled": True})

    names = _chat_tool_names()
    assert "probe_tool" in names
    assert "record_mood_journal" in names          # 内置工具照旧

    client.post(f"/plugins/{plugin_id}/enabled", json={"enabled": False})
    assert "probe_tool" not in _chat_tool_names()  # 禁用即下线


def test_plugin_tool_is_executable(client: TestClient, make_tool_plugin) -> None:
    """接进注册表还不够——工具要能真的执行（参数校验与异常隔离走同一条路）。"""
    from app.api.chat import _build_tool_registry
    from app.tools.registry import ToolContext

    plugin_id = make_tool_plugin("probe-tools-b")
    client.post(f"/plugins/{plugin_id}/enabled", json={"enabled": True})

    result = _build_tool_registry().execute("probe_tool", {}, ToolContext(companion_id="c"))
    assert result.success is True
    assert result.content == "探针工具已执行"

    client.post(f"/plugins/{plugin_id}/enabled", json={"enabled": False})


def test_builtin_tool_contributions_do_not_duplicate(client: TestClient) -> None:
    """`tools-builtin` 贡献的就是内置那 4 个工具：重名被跳过，而不是抛「工具名重复」。

    它是「能力账本」而非运行时唯一通路（`AGENTS.md §9.11` 的 P2 补充），
    因此内置工具由 `build_default_registry()` 恒定提供，插件那一份被忽略。
    """
    names = _chat_tool_names()
    assert names.count("record_mood_journal") == 1
    assert names.count("recall_memory") == 1


def test_enabling_tool_plugin_rebuilds_the_chat_graph(
    client: TestClient, make_tool_plugin
) -> None:
    """启用贡献工具的插件 → 对话图失效，否则要重启后端才生效。"""
    from app.api import chat as chat_module

    plugin_id = make_tool_plugin("probe-tools-c")
    before = chat_module.get_chat_graph()

    client.post(f"/plugins/{plugin_id}/enabled", json={"enabled": True})
    assert chat_module.get_chat_graph() is not before

    client.post(f"/plugins/{plugin_id}/enabled", json={"enabled": False})


def test_saving_plugin_settings_rebuilds_the_chat_graph(
    client: TestClient, make_tool_plugin
) -> None:
    """保存配置会重跑 `build()`——handler 可能闭包着旧快照。

    只比工具名不够：`tavern_library_status` 的数就是 build 那一刻算的，
    名字没变、数却变了，不重编会一直报改配置之前的数字。
    """
    from app.api import chat as chat_module

    plugin_id = make_tool_plugin("probe-tools-d")
    client.post(f"/plugins/{plugin_id}/enabled", json={"enabled": True})

    before = chat_module.get_chat_graph()
    response = client.put(f"/plugins/{plugin_id}/settings", json={"values": {}})
    assert response.status_code == 200
    assert chat_module.get_chat_graph() is not before

    client.post(f"/plugins/{plugin_id}/enabled", json={"enabled": False})


def test_toggling_plugin_without_tools_keeps_the_chat_graph(
    client: TestClient, isolated_manager
) -> None:
    """启停一个不贡献工具的插件**不该**重建对话图——那会连带重编码世界书向量索引。"""
    from app.api import chat as chat_module

    client.post("/plugins/tokenizer/enabled", json={"enabled": False})
    before = chat_module.get_chat_graph()

    client.post("/plugins/tokenizer/enabled", json={"enabled": True})
    assert chat_module.get_chat_graph() is before

    # 恢复默认启用，避免把状态留给后面的用例
    client.post("/plugins/tokenizer/enabled", json={"enabled": True})


def test_disabling_tavern_plugin_stops_serving_status(
    client: TestClient, isolated_manager
) -> None:
    """禁用后 `/status` 回到 409（而不是拿着停跑插件留下的数据源继续读用户目录）。"""
    client.post("/plugins/tavern-bridge/enabled", json={"enabled": True})
    assert client.get("/plugins/tavern-bridge/status").status_code == 200

    client.post("/plugins/tavern-bridge/enabled", json={"enabled": False})
    assert client.get("/plugins/tavern-bridge/status").status_code == 409


def test_tavern_switches_apply_without_restart(
    client: TestClient, isolated_manager
) -> None:
    """插件的两个开关与宿主 `Settings` 同名字段 → 存进插件配置即生效（§9.5）。

    这条用例守的是「声明了 ≠ 生效了」：schema 里多一个键名写错的字段，
    界面会照样渲染、保存也成功，但它永远不会变成运行时配置。
    两个都得测：`tavern_sync_on_startup` 尤其容易漏——它只在启动期被读一次，
    键名写错了本机根本发现不了。
    """
    from app.config import get_settings

    client.post("/plugins/tavern-bridge/enabled", json={"enabled": True})
    response = client.put(
        "/plugins/tavern-bridge/settings",
        json={"values": {"tavern_worldbook_enabled": True, "tavern_sync_on_startup": True}},
    )

    assert response.status_code == 200
    values = response.json()["values"]
    assert values["tavern_worldbook_enabled"] is True
    assert values["tavern_sync_on_startup"] is True
    assert get_settings().tavern_worldbook_enabled is True
    assert get_settings().tavern_sync_on_startup is True

    client.post("/plugins/tavern-bridge/enabled", json={"enabled": False})


def test_worldbook_switch_rebuilds_the_chat_graph(isolated_manager) -> None:
    """★ 开关变了必须重编对话图：世界书条目集是图的构造期快照（含一次性编码的向量索引）。

    不重编的表现就是「界面开了开关、对话还是没带上酒馆设定」——与工具那条路
    同一个病因（构造期快照 + 没有失效）。

    这里直接驱动「运行时覆盖层 → 失效」这条链，而不走 `PUT /settings`：
    后者会**同时**触发「插件工具变了」那条失效，就分不清是哪条在起作用了。
    """
    from app.api import chat as chat_module
    from app.api.plugins import _invalidate_consumers_if_changed
    from app.config import replace_runtime_overrides, runtime_overrides

    graph = chat_module.get_chat_graph()

    # 负面：改了不相干的运行时配置（TTS 音色）不该重建世界书向量索引
    before = runtime_overrides()
    replace_runtime_overrides({**before, "gpt_sovits_default_voice": "demo-voice"})
    _invalidate_consumers_if_changed(before)
    assert chat_module.get_chat_graph() is graph

    # 正面：酒馆世界书开关变了就得重建（下次对话重新问 get_worldbook_entries）
    before = runtime_overrides()
    replace_runtime_overrides({**before, "tavern_worldbook_enabled": True})
    _invalidate_consumers_if_changed(before)
    assert chat_module.get_chat_graph() is not graph


def test_budget_change_rebuilds_the_chat_graph(isolated_manager) -> None:
    """★ 注入预算改了也得重编图：PromptManager 在构造期快照了各层预算。

    不重编的表现是「界面上把预算调大了、注入量一点没变」——与工具/世界书那条路
    同一个病因（构造期快照 + 没有失效）。
    """
    from app.api import chat as chat_module
    from app.api.plugins import _invalidate_consumers_if_changed
    from app.config import replace_runtime_overrides, runtime_overrides

    graph = chat_module.get_chat_graph()

    before = runtime_overrides()
    replace_runtime_overrides({**before, "prompt_total_budget": 9000})
    _invalidate_consumers_if_changed(before)
    assert chat_module.get_chat_graph() is not graph

    # 真的生效了（而不仅仅是图重建）
    from app.config import get_settings

    assert get_settings().prompt_total_budget == 9000


def test_all_budget_fields_are_overridable() -> None:
    """白名单与 `BUDGET_CONFIG_FIELDS` 必须一一对应。

    少一个就是「界面填了、保存成功、但一点都不生效」——最难查的那类。
    """
    from app.api.chat import BUDGET_CONFIG_FIELDS
    from app.config import RUNTIME_OVERRIDABLE_FIELDS

    assert set(BUDGET_CONFIG_FIELDS) <= RUNTIME_OVERRIDABLE_FIELDS
