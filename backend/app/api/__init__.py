"""FastAPI 路由层。

- chat.py        POST /chat（完整对话链路：召回 + 生成 + 情绪 + 写入）+ 清单类接口
- st_presets.py  /chat/st-presets/*（酒馆预设的导入 / 编辑 / 导出）
- llm.py         /llm/*（对话模型的运行时切换，OpenAI 兼容）
- media.py       POST /media/avatar（数字人驱动数据）+ GET /media/audio/{file}
- knowledge.py   POST /knowledge/upload + GET /knowledge/list + DELETE /knowledge/{doc_id}
"""

from app.api.chat import router as chat_router
from app.api.knowledge import router as knowledge_router
from app.api.llm import router as llm_router
from app.api.media import router as media_router
from app.api.st_presets import router as st_presets_router

__all__ = [
    "chat_router",
    "knowledge_router",
    "llm_router",
    "media_router",
    "st_presets_router",
]
