"""从 SillyTavern 补全预设（completion preset JSON）提取**可合规复用**的部分。

合规边界（务必遵守 AGENTS.md §6）：
    只提取【采样参数】与【槽位骨架】（字段名 / 角色 / 注入位置 / 启用状态），
    **完全丢弃所有提示词正文（content）**。社区预设的提示词原文不属于本项目，
    复制入库会违反「借鉴社区插件只取机制思想，不复制代码与提示词原文」的红线。

因此本脚本产出的草稿里：
    - 采样参数：原样映射（这些是数值配置，非表达性内容）；
    - 槽位骨架：只保留「有哪些槽位、注入到哪、是否启用」的结构信息，
      用于人工判断「我们是否需要类似的注入位置」，正文一律留空由项目自写。

用法：
    python scripts/import_st_preset.py <preset.json>
    python scripts/import_st_preset.py <preset.json> --preset-id deepseek --model-match deepseek
    python scripts/import_st_preset.py <preset.json> --out app/llm/profiles.draft.yaml

默认只打印到标准输出，不写文件（避免草稿误入库）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------
# 采样参数映射：SillyTavern 字段 → 本项目 ModelProfile 字段
# 值为 None 表示本项目 provider 暂不透传（只作报告提示）
# ---------------------------------------------------------------
_SAMPLING_MAP: dict[str, str | None] = {
    "temperature": "temperature",
    "top_p": "top_p",
    "frequency_penalty": "frequency_penalty",
    "presence_penalty": "presence_penalty",
    "openai_max_tokens": "max_tokens",
    # 以下字段本项目 provider 暂不透传，仅列出以便人工取舍
    "min_p": None,
    "top_k": None,
    "top_a": None,
    "repetition_penalty": None,
    "openai_max_context": None,
    "seed": None,
}

# 推理档位：SillyTavern 用 show_thoughts / reasoning_effort 表达
_REASONING_KEYS = ("show_thoughts", "reasoning_effort", "verbosity", "tool_reasoning_mode")


def load_preset(path: Path) -> dict:
    """读取预设 JSON（容忍 UTF-8 BOM）。"""
    text = path.read_text(encoding="utf-8-sig")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} 顶层不是对象，可能不是 SillyTavern 预设")
    return data


def extract_sampling(data: dict) -> tuple[dict, list[str], dict]:
    """提取采样参数。

    返回: (可映射的采样参数, 未映射字段的说明行, 推理档位原始值)
    """
    sampling: dict[str, float | int] = {}
    notes: list[str] = []
    for key, mapped in _SAMPLING_MAP.items():
        if key not in data:
            continue
        value = data[key]
        if mapped is None:
            notes.append(f"#   {key} = {value!r}（本项目 provider 暂不透传，如需请扩展 _extra_body）")
        else:
            sampling[mapped] = value
    reasoning = {key: data[key] for key in _REASONING_KEYS if key in data}
    return sampling, notes, reasoning


def infer_enable_thinking(reasoning: dict) -> bool | None:
    """由推理档位推断本项目的 enable_thinking。

    SillyTavern 的 show_thoughts=false（不展示思考）≈ 我们希望关闭思考链以提速；
    无相关字段时返回 None（不传该参数，兼容普通模型）。
    """
    if "show_thoughts" not in reasoning:
        return None
    return not bool(reasoning["show_thoughts"])


def extract_slots(data: dict) -> list[dict]:
    """提取槽位骨架：**不含任何提示词正文**。

    返回形如 [{"identifier", "name", "role", "position", "depth", "enabled"}]
    """
    prompts = data.get("prompts") or []
    order = (data.get("prompt_order") or [{}])[0].get("order") or []
    enabled_map = {
        item.get("identifier"): bool(item.get("enabled"))
        for item in order
        if isinstance(item, dict)
    }
    by_id = {p.get("identifier"): p for p in prompts if isinstance(p, dict)}

    slots: list[dict] = []
    for identifier in enabled_map:
        raw = by_id.get(identifier, {})
        slots.append(
            {
                "identifier": identifier,
                "name": raw.get("name") or identifier,
                "role": raw.get("role") or "",
                "position": raw.get("injection_position"),
                "depth": raw.get("injection_depth"),
                "enabled": enabled_map[identifier],
            }
        )
    return slots


def build_draft(
    data: dict,
    *,
    source_name: str,
    preset_id: str,
    label: str,
    model_match: list[str],
) -> tuple[str, int]:
    """生成我方格式的预设草稿 YAML（不含任何提示词正文）。

    返回: (YAML 文本, 被丢弃的提示词正文条目数)
    """
    sampling, notes, reasoning = extract_sampling(data)
    enable_thinking = infer_enable_thinking(reasoning)
    slots = extract_slots(data)
    prompt_count = len(data.get("prompts") or [])
    discarded_chars = sum(
        len(p.get("content") or "") for p in (data.get("prompts") or []) if isinstance(p, dict)
    )

    lines: list[str] = []
    lines.append("# =============================================================")
    lines.append("# 由 scripts/import_st_preset.py 生成的**草稿**（请人工复核后再入库）")
    lines.append(f"# 来源文件：{source_name}")
    lines.append("# 合规声明：本草稿只含采样参数与槽位骨架，")
    lines.append(f"#           已丢弃 {prompt_count} 条提示词正文（共 {discarded_chars} 字符）——不复制社区提示词原文。")
    lines.append("# =============================================================")
    lines.append("")
    lines.append("profiles:")
    lines.append(f"  - id: {preset_id}")
    lines.append(f"    label: {label}")
    lines.append("    description: 由 SillyTavern 预设的采样参数提取（正文未导入，槽位见文件末尾注释）")
    lines.append(f"    match: [{', '.join(repr(m) for m in model_match)}]")
    for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty", "max_tokens"):
        if key in sampling:
            lines.append(f"    {key}: {sampling[key]}")
    if enable_thinking is not None:
        lines.append(f"    enable_thinking: {str(enable_thinking).lower()}")
    lines.append("    style_hint: |")
    lines.append("      （请在此写本项目自有的技术性输出约束，不要复制社区预设的提示词原文）")

    if notes:
        lines.append("")
        lines.append("# ---- 未映射的原始字段（供人工取舍）----")
        lines.extend(notes)

    if reasoning:
        lines.append("")
        lines.append("# ---- 推理档位原始值（仅参考）----")
        for key, value in reasoning.items():
            lines.append(f"#   {key} = {value!r}")

    if slots:
        lines.append("")
        lines.append("# ---- 槽位骨架（仅结构信息，无正文）----")
        lines.append("#   用途：对照我们的分层提示词，判断是否需要新增注入位置；")
        lines.append("#   identifier | role | position | depth | enabled")
        for slot in slots:
            lines.append(
                f"#   {slot['identifier']} | {slot['role']} | "
                f"{slot['position']} | {slot['depth']} | {slot['enabled']}"
            )

    return "\n".join(lines) + "\n", prompt_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从 SillyTavern 补全预设提取采样参数与槽位骨架（丢弃全部提示词正文）"
    )
    parser.add_argument("preset", type=Path, help="SillyTavern 预设 JSON 路径")
    parser.add_argument("--preset-id", default="imported", help="生成的档位 id")
    parser.add_argument("--label", default="", help="界面展示名（缺省用档位 id）")
    parser.add_argument(
        "--model-match",
        nargs="*",
        default=["*"],
        help="模型名匹配模式（子串），默认通配",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出 YAML 路径；缺省只打印到标准输出（不落盘）",
    )
    args = parser.parse_args(argv)

    if not args.preset.exists():
        print(f"文件不存在：{args.preset}", file=sys.stderr)
        return 1

    data = load_preset(args.preset)
    draft, discarded = build_draft(
        data,
        source_name=args.preset.name,
        preset_id=args.preset_id,
        label=args.label or args.preset_id,
        model_match=args.model_match,
    )

    if args.out:
        args.out.write_text(draft, encoding="utf-8")
        print(f"草稿已写入 {args.out}", file=sys.stderr)
    else:
        print(draft)

    print(
        f"\n[合规] 已丢弃 {discarded} 条提示词正文（未写入草稿）。"
        " 如需要类似效果，请按本项目风格自行撰写。",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
