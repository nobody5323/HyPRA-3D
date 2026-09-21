"""knowledge 子包：个人记忆（用户上传的私人语料）。

个人记忆与情景记忆（对话片段）**共用同一套混合检索底座**
（`app/rag/retrieval/`：BM25 + 稠密向量 → RRF），区别在于：

| | 情景记忆（warm） | 个人记忆（knowledge） |
|---|---|---|
| 来源 | AI 从对话中自动记录 | **用户主动上传** |
| 时间衰减 | 有（30 天半衰期） | **无**——知识不老化 |
| 情绪加权 | 有 | **无**——知识是客观的 |
| 淘汰 | `last_recalled_at` 超 TTL 即删 | 用户主动删除 |

模块构成（随实施进度补齐）：

- cleaner.py  清洗去噪（去格式残留，保留 Markdown 结构）
- dedup.py    三级去重（文件 MD5 / 内容 MD5 / MinHash 近似）
- chunker.py  分块（递归字符 + 语义边界 + overlap）
- parser.py   格式解析（txt / md / pdf / docx）
"""
