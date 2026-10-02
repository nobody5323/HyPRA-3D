"""适配规则加载器：规则 YAML 与代码分离。

预设适配的「体检表」写在 `rules.yaml` 里（改规则不用动代码），本模块负责：
- 定位并读取规则文件；
- 用 pydantic 做 schema 校验（字段缺失/正则写错即报错，早失败）。

缓存策略：按**规则文件的 mtime** 缓存。既不每次请求重解析，又能在改完
`rules.yaml` 后自动生效——纯 `lru_cache` 会让文件改动在进程重启前完全不可见，
调规则时极容易误以为「规则没生效」。

测试要改规则时用 `load_rules_file(path)` 直接读指定文件。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from app.paths import resource_path
from app.prompts.adaptation.models import AdaptationRules


def rules_file() -> Path:
    """内置适配规则文件（随程序分发的只读资源）。"""
    return resource_path("app", "prompts", "adaptation", "rules.yaml")

#: (mtime, 规则) —— mtime 变了就重新解析
_rules_cache: tuple[float, AdaptationRules] | None = None


def load_rules_file(file_path: str | Path) -> AdaptationRules:
    """加载并校验指定路径的规则 YAML。"""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"适配规则文件不存在：{path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"规则文件 {path.name} 顶层必须是映射（dict）")

    try:
        return AdaptationRules.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"规则文件 {path.name} 校验失败：{exc}") from exc


def load_rules() -> AdaptationRules:
    """加载内置适配规则（按文件 mtime 缓存）。

    改完 `rules.yaml` 无需重启后端，下次调用即生效；文件不可读时回退到上次
    成功的解析结果（比整条适配链路报错更可取）。
    """
    global _rules_cache
    target = rules_file()
    try:
        mtime = target.stat().st_mtime
    except OSError:
        if _rules_cache is None:
            raise
        return _rules_cache[1]

    if _rules_cache is None or _rules_cache[0] != mtime:
        _rules_cache = (mtime, load_rules_file(target))
    return _rules_cache[1]
