"""插件管理 API（设计见 `AGENTS.md §9`）。

    GET    /plugins                    插件列表 / 状态 / 能力索引 / 扫描目录（能力中心数据源）
    POST   /plugins/{id}/enabled       启用 / 禁用（core 层拒绝禁用）
    GET    /plugins/{id}/settings      读插件配置（含 settings_schema，前端据此渲染表单）
    PUT    /plugins/{id}/settings      写插件配置

    POST   /plugins/import             把**写在别处**的插件目录导入到用户插件目录
    POST   /plugins/reload             重扫插件目录（发现新放进去的插件；不重跑已加载插件的代码）

    POST   /plugins/tavern-bridge/import    酒馆会话 → 长期记忆（P4：跨会话记忆构建）

插件是**用户自己写**的（接口见 `docs/plugin-development.md`，示例见
`docs/examples/plugin-hello/`）：宿主不生成插件代码，只负责加载与权限强制。
两条接入路径——零拷贝（`PLUGIN_EXTRA_DIRS` 指向插件所在目录）与导入（本文件
`POST /plugins/import`，把插件目录复制进 `PLUGINS_DIR`）。

**配置是声明式的**：插件不写前端表单，只给 JSON Schema，前端按 schema 渲染——
这是 `§9.5` 的落地（对标 Alife 用 `editorUI` 类型贡献 UI 的做法，但避免了运行时加载前端代码）。

三条**生效与安全**约定（细节见 app/plugins/settings_runtime.py）：

1. schema 键名与宿主 `Settings` 字段同名时成为**运行时覆盖**（界面设置 > .env），
   因此表单里填完保存就生效，不必改 .env 或重启（步骤见 setup_all → apply_runtime_overrides）；
2. `format: "password"` 的字段**永不回传明文**：GET 只回 `secrets_set`（有没有），
   PUT 留空 = 沿用、`null` = 清除（口径与 `/llm/config` 一致）；
3. 配置真的变了才重建运行时单例（embedding provider / 数字人驱动）：
   改一个没有运行时配置的插件（如工具集）不该把 embedding 客户端白白重建一遍。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import (
    effective_overridable_values,
    get_settings,
    plugin_extra_dirs_list,
    runtime_overrides,
)
from app.memory.tavern_import import TavernMemoryImporter
from app.plugins.loader import PluginImportError, import_plugin_dir, plugin_dir
from app.plugins.manager import get_plugin_manager
from app.plugins.settings_runtime import (
    merge_secret_fields,
    overridable_fields,
    properties_of,
    settings_view,
)
from app.prompts.persona.datasource_map import scopes_by_character_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])

TAVERN_PLUGIN_ID = "tavern-bridge"


# =============================================================
# 请求模型
# =============================================================


class PluginToggleRequest(BaseModel):
    enabled: bool = Field(description="是否启用")


class PluginSettingsPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict, description="配置键值（按 settings_schema）")


class PluginImportRequest(BaseModel):
    """导入一份写在别处的插件目录（`AGENTS.md §9.3`）。"""

    path: str = Field(
        min_length=1,
        description="插件源目录（其下须有 manifest.json），如 D:/my-plugins/water-tracker",
    )
    replace: bool = Field(default=False, description="目标已存在时是否整体替换（默认拒绝）")


# =============================================================
# 接入写在别处的插件（AGENTS.md §9.3）
# =============================================================
#
# 两条路，都不涉及「宿主生成代码」：
#
# - 零拷贝：把插件所在目录填进 PLUGIN_EXTRA_DIRS（.env），discover() 直接扫它——
#   开发期最顺手，改完代码点一次「重新扫描」即可，宿主不碰你的文件；
# - 导入：`POST /plugins/import` 把插件目录复制进 PLUGINS_DIR，适合「拿到一份插件包」。
#
# 导入本身**不做任何静态安全分析**：插件在宿主进程内执行（§8.5 / §9.7 已明确的
# 「本地可信」边界），宿主的防线是「权限声明强制 + 用户自己写 / 自己审阅」，
# 不是「看起来检查过了」。界面上不得暗示有沙箱。


@router.post("/import")
def import_plugin(req: PluginImportRequest) -> dict:
    """把一份插件目录导入到用户插件目录，并当场重扫让它出现在能力中心里。"""
    manager = get_plugin_manager()
    settings = get_settings()
    # 只把「真的占着位置」的 id 当成冲突：
    # - 内置插件（`entry_path is None`，代码内注册）：永远算占用；
    # - 目录插件：目录还在才算——用户把 `data/plugins/<id>/` 手动删掉后想重新导入同一个 id，
    #   不该被一条“幽灵登记”（注册表只增不删）永久拦住（否则只能重启后端）。
    known_ids = {
        reg.manifest.id
        for reg in manager.registry.all()
        if reg.entry_path is None or reg.entry_path.exists()
    }
    try:
        manifest = import_plugin_dir(
            req.path,
            settings.plugins_dir,
            known_ids=known_ids,
            replace=req.replace,
        )
    except PluginImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 重扫只为「发现」新目录（只读 manifest，不执行插件代码）
    manager.discover()
    logger.info("插件 %s 已导入：%s", manifest.id, plugin_dir(settings.plugins_dir, manifest.id))
    return {
        "id": manifest.id,
        # 目录拼法只有一处（loader.plugin_dir）：免得界面显示的路径与实际落盘的不一致
        "path": str(plugin_dir(settings.plugins_dir, manifest.id)),
        "enabled": manager.registry._is_enabled(manifest.id),  # noqa: SLF001 - 状态视图同源
        "plugins": manager.status(),
    }


@router.post("/reload")
def reload_plugins() -> dict:
    """重扫插件目录，发现新放进去的插件（含 `PLUGIN_EXTRA_DIRS` 指向的目录）。

    为什么这不是 §9.7 说的「热重载」：本端点只读 `manifest.json` 并登记插件，
    **不重新导入、不重跑**任何已加载插件的代码（那是开发期 `uvicorn --reload` 的事）。
    需求很具体：用户刚把插件目录放到某个位置（或改了 `PLUGIN_EXTRA_DIRS`），
    不重启后端就该在能力中心看到它。
    """
    manager = get_plugin_manager()
    found = manager.discover()
    return {
        "discovered": [reg.manifest.id for reg in found],
        "plugins": manager.status(),
    }


# =============================================================
# 列表与启停
# =============================================================


def _plugin_dirs_view() -> dict[str, Any]:
    """插件扫描目录视图（界面据此告诉用户「插件放哪儿」）。"""
    settings = get_settings()
    return {
        # 用户目录：把插件目录丢进来，点「重新扫描」即可（最常见的一条路）
        "user_dir": str(settings.plugins_dir),
        # 额外来源目录：开发期零拷贝接入（PLUGIN_EXTRA_DIRS）
        "extra_dirs": plugin_extra_dirs_list(settings),
        # 第一方目录：随项目分发，界面只读展示
        "builtin_dir": str(settings.builtin_plugins_dir),
    }


@router.get("")
def list_plugins() -> dict:
    """插件列表（含状态、分层、能力索引与扫描目录）——`/health` 的完整版。"""
    manager = get_plugin_manager()
    return {
        "plugins": manager.status(),
        "summary": manager.summary(),
        "capabilities": manager.registry.capabilities_summary(),
        "dirs": _plugin_dirs_view(),
    }


@router.post("/{plugin_id}/enabled")
def set_plugin_enabled(plugin_id: str, payload: PluginToggleRequest) -> dict:
    """启用 / 禁用插件。core 层不可禁用（返回 400）。

    状态**落盘**（`data/plugins/<id>/state.json`）：重启后保持，不会回弹到 manifest 默认值。
    启停同时立即生效——启用即 setup、禁用即停跑，界面与实际一致。

    启停也可能改变运行时覆盖（插件的声明式配置生效/失效），因此同样比对前后差异。
    """
    manager = get_plugin_manager()
    before = runtime_overrides()
    tools_before = _plugin_tool_names()
    try:
        manager.set_enabled(plugin_id, payload.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"插件不存在：{plugin_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _invalidate_consumers_if_changed(before)
    _invalidate_chat_graph_if_plugin_tools_changed(tools_before)
    return {"id": plugin_id, "enabled": payload.enabled, "plugins": manager.status()}


# =============================================================
# 配置
# =============================================================


def _reconcile_activity_switch(changed: set[str]) -> None:
    """行踪相关配置变了 → 立刻把「开关」对账到磁盘（关掉就清空记录）。

    为什么必须在这一刻做，而不是等桌宠端下一次上报：
    **用户完全可能「先在控制台关掉开关，再关掉桌宠窗」**——那时最后一次上报
    已经发生过，文件会一直留在盘上。而用户关掉这个开关的动机通常就是
    「我不想要这些了」，不是「从今往后别再写」。留着 = 没关干净。

    与 `_invalidate_consumers_if_changed` 同一口径：字段清单由**感知层自己**声明
    （`app/api/perception.py: ACTIVITY_CONFIG_FIELDS`），这里只做比对——
    避免在 API 层再抄一份字段清单。
    """
    from app.api.perception import ACTIVITY_CONFIG_FIELDS  # noqa: PLC0415
    from app.perception.timeline import sync_activity_switch  # noqa: PLC0415

    if not changed & ACTIVITY_CONFIG_FIELDS:
        return
    try:
        sync_activity_switch()
    except Exception as exc:  # noqa: BLE001 - 清理失败不该让「保存配置」整体失败
        logger.warning("行踪开关对账失败（已忽略）：%s", exc)


def _invalidate_consumers_if_changed(before: dict[str, Any]) -> None:
    """运行时覆盖变了才重建相关消费方单例（embedding provider / 数字人驱动）。

    两者都是进程内单例，且构造时会快照配置：不重建的话「保存了配置但不生效，
    要重启后端」。重建是按需的（下次用到才建），所以这里只做失效。

    **只失效真的受影响的那个**：重建 embedding 会连带重建记忆门面与已编译的图
    （世界书向量要重新编码一次），所以“改了个 TTS 音色”不该把它也拖下水。
    两边各自声明了自己的配置依赖（`EMBEDDING_CONFIG_FIELDS` /
    `DIGITAL_HUMAN_CONFIG_FIELDS`），这里只做比对——避免在 API 层再抄一份字段清单。

    嵌入式动作放在 API 层而非插件框架层：`app/plugins/` 不该知道
    `app/api/chat.py` 这类业务模块的存在（`AGENTS.md §3` 的依赖方向）。
    """
    after = runtime_overrides()
    changed = {
        key for key in set(before) | set(after) if before.get(key) != after.get(key)
    }
    if not changed:
        return

    _reconcile_activity_switch(changed)

    from app.api import chat as chat_module  # noqa: PLC0415 - 避免模块级循环导入
    from app.api import knowledge as knowledge_module  # noqa: PLC0415
    from app.api import media as media_module  # noqa: PLC0415

    if changed & set(chat_module.EMBEDDING_CONFIG_FIELDS):
        chat_module.set_embedding_provider(None)
        # 知识库存储也在构造时**快照**了 embedding provider（`app/api/knowledge.py`），
        # 不重置的话：温层用新 provider、个人记忆用旧 provider（旧模型 / 旧 key / 旧维度）
        # ——既违背「两处必须同一向量空间」的约定，也变成「改了 key 却只有一半生效」。
        knowledge_module.set_knowledge_store(None)
    if changed & set(chat_module.WORLDBOOK_CONFIG_FIELDS):
        # 世界书条目集是图的构造期快照（含一次性编码的向量索引），只失效就够了——
        # 下次对话重建时会重新问 `get_worldbook_entries()`，顺带把酒馆条目读进来。
        chat_module.invalidate_chat_graph()
    if changed & set(chat_module.BUDGET_CONFIG_FIELDS):
        # PromptManager 的各层预算同样是构造期快照：不重编就是「改了预算、注入量没变」。
        chat_module.invalidate_chat_graph()
    if changed & set(media_module.DIGITAL_HUMAN_CONFIG_FIELDS):
        media_module.set_digital_human_provider(None)
    logger.info(
        "插件配置改变了运行时配置（%s），已失效相关单例", ", ".join(sorted(changed))
    )


def _plugin_tool_names() -> tuple[str, ...]:
    """当前启用插件贡献的工具名（排序后；用于比对启停是否改变了工具集）。"""
    return tuple(spec.name for spec in get_plugin_manager().registry.tools())


def _invalidate_chat_graph_if_plugin_tools_changed(
    before: tuple[str, ...], *, reloaded: str = ""
) -> None:
    """插件工具变了才重编对话图（插件的 `tool` 能力面，`AGENTS.md §9.4`）。

    对话图在构造时**快照**了工具注册表（`app/api/chat.py:_build_tool_registry`），
    不失效的话「启用插件 → 工具还是不出现」，得重启后端才生效。

    两种必须失效的情形：

    1. 工具**集合**变了——启停一个贡献工具的插件；
    2. 集合没变但插件**重跑了 `build()`**（传 `reloaded`）——handler 可能闭包着旧快照，
       例如 `tavern_library_status` 的计数是 build 那一刻算的，不重编就一直报旧数字。

    反过来，启停一个不贡献工具的插件（TTS / embedding / tokenizer）**不该**重建
    对话图——那会连带重编码一遍世界书向量索引。所以这里只比工具名，不做无条件失效。
    """
    registration = get_plugin_manager().registry.get(reloaded) if reloaded else None
    rebuilt_tools = registration is not None and bool(registration.tools)
    if not rebuilt_tools and _plugin_tool_names() == before:
        return

    from app.api import chat as chat_module  # noqa: PLC0415 - 避免模块级循环导入

    chat_module.invalidate_chat_graph()
    logger.info("插件工具变化，已失效对话图（下次对话按最新工具集重建）")


def _settings_response(manager: Any, plugin_id: str, schema: dict, saved: dict) -> dict:
    """配置的**安全视图**（GET 与 PUT 共用同一形状：前端不必处理两套）。

    - 未保存的字段用宿主当前生效值回填 → 界面显示的应当就是实际在用的；
    - 密钥字段只回 `secrets_set`，明文永不出现在响应里。
    """
    defaults = effective_overridable_values(overridable_fields(properties_of(schema).keys()))
    values, secrets_set = settings_view(schema, saved, defaults=defaults)
    return {"id": plugin_id, "values": values, "secrets_set": secrets_set}


@router.get("/{plugin_id}/settings")
def get_plugin_settings(plugin_id: str) -> dict:
    """读插件配置 + 其 settings_schema（前端据此渲染表单）。"""
    manager = get_plugin_manager()
    registration = manager.registry.get(plugin_id)
    if registration is None:
        raise HTTPException(status_code=404, detail=f"插件不存在：{plugin_id}")
    schema = registration.manifest.settings_schema
    view = _settings_response(manager, plugin_id, schema, manager._load_settings(plugin_id))  # noqa: SLF001
    return {
        **view,
        "schema": schema,
        "permissions": registration.manifest.permissions.model_dump(mode="json"),
    }


@router.put("/{plugin_id}/settings")
def put_plugin_settings(plugin_id: str, payload: PluginSettingsPayload) -> dict:
    """保存插件配置；保存后立刻重新 setup，让新配置生效。

    密钥字段不直接落盘：先与已保存值合并（留空沿用 / `null` 清除），
    否则「只改了模型名」会把没回填的 key 写成空。
    """
    manager = get_plugin_manager()
    registration = manager.registry.get(plugin_id)
    if registration is None:
        raise HTTPException(status_code=404, detail=f"插件不存在：{plugin_id}")

    schema = registration.manifest.settings_schema
    saved = manager._load_settings(plugin_id)  # noqa: SLF001 - 同一模块族的读接口
    merged = merge_secret_fields(schema, saved, payload.values)

    before = runtime_overrides()
    tools_before = _plugin_tool_names()
    manager.save_settings(plugin_id, merged)
    # 目录插件的 build(ctx) 会**重跑**（如数据目录变更后重新读取）；
    # 用 reload_all 而非 setup_all：只 setup 会把插件退回 LOADED，
    # 界面上的「运行中」会变成「已加载」——刚存完配置就像能力掉了。
    manager.reload_all()
    _invalidate_consumers_if_changed(before)
    _invalidate_chat_graph_if_plugin_tools_changed(tools_before, reloaded=plugin_id)

    return {
        **_settings_response(manager, plugin_id, schema, merged),
        "plugins": manager.status(),
    }


@router.post("/tavern-bridge/knowledge/sync")
def sync_tavern_knowledge() -> dict:
    """把酒馆世界书同步进**个人知识库**（按角色分作用域，覆盖写）。

    酒馆世界书统一走知识库路径，不再有「每轮直接注入」分支：
    - 用户勾选的来源写入个人知识库；
    - 每轮通过向量 + BM25 检索，只有相关片段才进上下文；
    - 未命中的世界书不占提示词预算。

    274 条世界书不再塞进 400 token 的常驻注入预算；作用域与世界书 `scope`、
    酒馆会话记忆**同一套 id**（全局书归共享作用域）。
    """
    from app.api.knowledge import get_knowledge_store
    from app.memory.knowledge.datasource_sync import sync_worldbook_knowledge

    source = _find_tavern_datasource()
    snapshot = source.read()
    result = sync_worldbook_knowledge(get_knowledge_store(), snapshots=[snapshot])
    # 同步后知识库分块数会变，检索器的稀疏索引需要重建（它按 chunk 数自检，
    # 但同长度覆盖不会触发——这里显式失效一次）
    _invalidate_knowledge_caches()
    return {"root": str(source.root) if source.root else "", **result.as_dict()}


@router.post("/tavern-bridge/knowledge/clear")
def clear_tavern_knowledge() -> dict:
    """删除知识库里**由酒馆世界书同步进来的**全部文档。

    只删 `source_type == "tavern-worldbook"` 的那些——用户自己上传的语料一篇不动。
    """
    from app.api.knowledge import get_knowledge_store
    from app.memory.knowledge.datasource_sync import (
        candidate_scopes,
        existing_doc_ids,
    )
    from app.plugins.datasources import read_snapshots

    store = get_knowledge_store()
    snapshots, _ = read_snapshots()
    removed = 0
    for scope in sorted(candidate_scopes(snapshots)):
        for doc_id in sorted(existing_doc_ids(store, scope)):
            try:
                store.delete_document(scope, doc_id)
            except Exception as exc:  # noqa: BLE001 - 单篇失败不阻断其余
                logger.warning("删除酒馆知识文档失败：%s（%s）", doc_id, exc)
                continue
            removed += 1
    _invalidate_knowledge_caches()
    return {"removed": removed}


def _invalidate_knowledge_caches() -> None:
    """让已编译对话图里的知识检索器缓存失效（下次对话重建）。

    不做的话：同步完知识库，分块数变了的那些作用域要等下一次自检才刷新；
    而**删除**路径下图的构造期快照也更旧。统一失效最简单也最不容易漏。
    """
    from app.api import chat as chat_module  # noqa: PLC0415 - 避免模块级循环导入

    chat_module.invalidate_chat_graph()


# =============================================================
# 酒馆会话 → 长期记忆（P4）
# =============================================================


def _find_tavern_datasource() -> Any:
    """取已接入的酒馆数据源（插件未启用/未配置时抛出可读错误）。"""
    manager = get_plugin_manager()
    registration = manager.registry.get(TAVERN_PLUGIN_ID)
    if registration is None:
        raise HTTPException(status_code=404, detail="酒馆接入插件未安装")
    if not registration.datasources:
        raise HTTPException(
            status_code=409,
            detail="酒馆接入插件尚未生效：请先在能力中心启用它并填写酒馆数据目录",
        )
    return registration.datasources[0]


@router.get("/tavern-bridge/status")
def tavern_bridge_status(
    companion_id: str = Query("", description="查询该陪伴对象已导入的会话记录"),
) -> dict:
    """酒馆接入状态：能读到什么 + 已导入多少（供 UI 显示进度）。"""
    source = _find_tavern_datasource()
    snapshot = source.read()

    imported: list[str] = []
    if companion_id:
        from app.config import get_settings

        importer = TavernMemoryImporter(
            memory_store=None,  # 仅查询记录，不写记忆
            state_path=get_settings().tavern_import_state_file,
        )
        imported = importer.imported_sessions(companion_id)

    from app.config import get_settings
    from app.memory.tavern_sync import pending_sync

    # 「待同步多少轮」与实际点下去的写入量共用同一套游标规则（见 tavern_sync 模块）
    pending_turns, pending_sessions, sync_warnings = pending_sync(
        companion_id=companion_id or "",
        state_path=get_settings().tavern_import_state_file,
    )

    knowledge: dict = {"documents": 0, "chunks": 0, "scopes": 0}
    if get_settings().knowledge_enabled:
        try:
            from app.api.knowledge import get_knowledge_store
            from app.memory.knowledge.datasource_sync import worldbook_knowledge_status

            knowledge = worldbook_knowledge_status(
                get_knowledge_store(), snapshots=[snapshot]
            )
        except Exception as exc:  # noqa: BLE001 - 知识库不可用不该让状态接口 500
            logger.warning("读取酒馆知识库状态失败：%s", exc)

    # 可选来源清单（供界面勾选）：插件自己会给出不受勾选影响的完整列表，
    # 否则取消勾选的项会从清单里消失、再也勾不回来
    catalog = getattr(source, "book_catalog", None)
    books = catalog() if callable(catalog) else []

    return {
        "root": str(source.root) if source.root else "",
        "available": snapshot.counts,
        "warnings": [*snapshot.warnings[:20], *sync_warnings][:20],
        "imported_sessions": imported,
        "characters": [character.name for character in snapshot.characters],
        # 保留旧字段避免前端/旧客户端解包失败；它不再代表直注入开关。
        # 酒馆世界书是否参与对话，取决于是否同步进知识库以及本轮是否检索命中。
        "worldbook_enabled": False,
        "pending_turns": pending_turns,
        "pending_sessions": pending_sessions,
        "knowledge": knowledge,
        "books": books,
    }


@router.post("/tavern-bridge/import")
def import_tavern_memory(
    companion_id: str = Query(..., description="导入到哪个陪伴对象的记忆命名空间"),
    force: bool = Query(False, description="忽略已导入记录，重新导入"),
) -> dict:
    """把酒馆会话导入为 HyPRA 的长期记忆（`AGENTS.md §8.2` 的跨会话记忆）。

    走同一条记忆写入链路（`MemoryStore.remember_turn`）：
    情景记忆进温层向量库、语义事实进冷层表——与日常对话产生的记忆同构，
    因此召回时无需区分来源。
    """
    from app.api.chat import get_memory_store
    from app.config import get_settings

    source = _find_tavern_datasource()
    memory_store = get_memory_store()
    if memory_store is None:
        raise HTTPException(status_code=503, detail="记忆服务未就绪")

    snapshot = source.read()
    importer = TavernMemoryImporter(
        memory_store, state_path=get_settings().tavern_import_state_file
    )
    result = importer.import_sessions(
        snapshot.sessions,
        companion_id=companion_id,
        # 酒馆里不同角色的剧情各进各的记忆：全部灌进一个陪伴对象的话，
        # 选谁都会召回别人的剧情（§8.2 的记忆隔离）。
        # 用与世界书 scope 同一个 id 函数，两边才不会挂到不同的 id 上。
        character_scopes=scopes_by_character_name(snapshot),
        force=force,
    )
    return {"companion_id": companion_id, **result.as_dict()}
