"""插件声明式配置与宿主运行时配置之间的桥（`AGENTS.md §9.5`）。

插件用 `settings_schema` 声明配置，宿主渲染表单、把值存进
`data/plugins/<id>/settings.json`。但「声明了」不等于「生效了」——本模块负责把
两边接上，只做四件事：

1. `collect_runtime_overrides` —— 收集要写进运行时覆盖层的值（界面设置 > .env，
   覆盖层本身与白名单见 `app/config.py`）；
2. `secret_fields` —— 认出密钥字段（`format: "password"`）：它的值**永不回传明文**；
3. `settings_view` —— GET 的视图：已保存值 + `.env` 当前值合并，密钥只回「有没有」；
4. `merge_secret_fields` —— PUT 的合并：密钥留空 = 沿用，显式 `null` = 清除（回落 .env）。

**键名约定**：schema 键名与 `Settings` 字段同名（`embedding_provider` 对应
`EMBEDDING_PROVIDER`）。于是 `.env.example`、界面字段、覆盖白名单三者一一对应，
用户不必学第二套命名；不同名的键（如 `tavern_dir`）就只是插件自己的配置，
不参与覆盖——这是**约定**而不是魔法，`app/config.py` 的白名单是最终裁决者。

关于依赖边界（§3 的「框架层不反向依赖业务」）：`registry` / `manifest` / `context`
保持与业务完全无关；本模块是**宿主侧**的配置桥，只依赖 `app.config` 的配置面
（不 import 任何 memory / llm / graph 等业务实现）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.config import RUNTIME_OVERRIDABLE_FIELDS, Settings, replace_runtime_overrides

#: 密钥字段的标记：schema 里 `format: "password"`（前端据此渲染密码框）。
SECRET_FORMAT = "password"


def properties_of(schema: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """取 schema 的 properties（缺声明时为空字典，调用方不必判空）。"""
    if not isinstance(schema, Mapping):
        return {}
    raw = schema.get("properties")
    return dict(raw) if isinstance(raw, Mapping) else {}


def secret_fields(schema: Mapping[str, Any] | None) -> list[str]:
    """schema 里声明为密钥的字段名。"""
    return [
        key
        for key, field in properties_of(schema).items()
        # 容错：手写的 manifest 可能把 properties 值写成非 dict
        if isinstance(field, Mapping) and field.get("format") == SECRET_FORMAT
    ]


def _has_value(value: Any) -> bool:
    """「有没有值」的判定：空串 / 空数组 / None 都算没有。

    与前端 `SchemaForm` 的「清空即删键」同语义：用户看到的空白 = 未配置。
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def collect_runtime_overrides(
    schema: Mapping[str, Any] | None,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """把插件配置里**能覆盖宿主配置**的项收集出来（留空 = 不覆盖，沿用 .env）。

    只做「schema 声明过 + 有值」的筛选；能不能覆盖由 `app/config.py` 的白名单裁决
    （越权的字段名在那里告警）——白名单属于宿主，不该散到插件框架里各写一份。
    """
    return {
        key: settings[key]
        for key, field in properties_of(schema).items()
        if isinstance(field, Mapping) and _has_value(settings.get(key))
    }


def settings_view(
    schema: Mapping[str, Any] | None,
    saved: Mapping[str, Any],
    *,
    defaults: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """GET `/plugins/{id}/settings` 的视图：(值, 已配置的密钥字段名)。

    三个刻意的选择：

    - **密钥字段的值永不出现**在任何返回值里（`secrets_set` 只说「有没有」）。
      这是 `/llm/config` 早就立下的口径（见 `app/api/llm.py` 文件头），插件配置
      没有理由更宽松——同一个浏览器里没必要多一条取明文 key 的路。
    - **未保存的字段用宿主当前生效值回填**（`defaults`）：界面显示的应当就是
      实际在用的。否则用户看到一个空表单，没法判断「现在到底跑的是哪个模型」。
      只回填 `app/config.py` 给出白名单内的字段（`tavern_dir` 这类插件私有配置没有宿主值）。
    - **已保存但 schema 里没有的键原样保留**：手改过配置文件的用户不该因为
      打开一次界面就被抹掉数据（写回时前端会把它一起提交）。
    """
    props = properties_of(schema)
    secrets = set(secret_fields(schema))
    provided = dict(defaults or {})

    values: dict[str, Any] = {key: value for key, value in saved.items() if key not in secrets}
    secrets_set: list[str] = []
    for key in secrets:
        if _has_value(saved.get(key)) or _has_value(provided.get(key)):
            secrets_set.append(key)

    for key in props:
        if key in secrets or key in values:
            continue
        if _has_value(provided.get(key)):
            values[key] = provided[key]
    return values, secrets_set


def merge_secret_fields(
    schema: Mapping[str, Any] | None,
    saved: Mapping[str, Any],
    submitted: Mapping[str, Any],
) -> dict[str, Any]:
    """合并密钥字段后待落盘的值。

    规则（前端拿不到明文，只能提交「要改的新值」或什么都不提交）：

    | 提交内容 | 语义 |
    | --- | --- |
    | 该键不存在 | 沿用已保存的（密码框没回填，用户没动它） |
    | `null` | **显式清除**：删掉该键，回落到 `.env` |
    | 空串 / 纯空白 | 等同「没填」→ 沿用 |
    | 其它 | 新值（字符串 strip 后写入） |

    没有这一层的话，「保存一次配置」会把没回填的密钥写成空——用户以为只是改了模型名，
    实际把 key 抹了。
    """
    merged = dict(submitted)
    for key in secret_fields(schema):
        if key not in submitted:
            if key in saved:
                merged[key] = saved[key]
            continue

        value = submitted[key]
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, str) and not value.strip():
            if key in saved:
                merged[key] = saved[key]
            else:
                merged.pop(key, None)
        elif isinstance(value, str):
            merged[key] = value.strip()
    return merged


def overridable_fields(fields: Iterable[str]) -> list[str]:
    """这些字段里，哪些是宿主允许运行时覆盖的（`.env` 回填与覆盖收集共用一处判断）。"""
    return [
        name
        for name in fields
        if name in RUNTIME_OVERRIDABLE_FIELDS and name in Settings.model_fields
    ]


def apply_runtime_overrides(values: Mapping[str, Any]) -> bool:
    """把收集到的插件配置写进运行时覆盖层（返回是否真的变了）。

    这是桥的写半边：插件宿主（`manager.setup_all`）只需要调它，
    不必知道覆盖层住在 `app.config` 里。
    """
    return replace_runtime_overrides(values)
