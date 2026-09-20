"""worldbook 子包：世界书条目（触发条件 → 注入内容）。

- models.py       条目数据模型（关键词 / 正则 / 语义向量三类触发条件）
- loader.py       YAML 加载与校验
- matcher.py      三通道命中判定（关键词 ∨ 正则 ∨ 语义向量）
- vector_index.py 语义向量索引（条目编码一次，检索只编码 query）
- entries/        自创演示条目

本包只负责「哪些条目命中」；命中后的 token 预算与注入编排见
app/prompts/assemble.py 与 app/rag/prompt_manager.py。
"""
