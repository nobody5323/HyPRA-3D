"""FastAPI 路由层。

- chat.py      POST /chat（完整对话链路：召回 + 生成 + 情绪 + 写入）
- media.py     POST /media/avatar（数字人驱动数据）+ GET /media/audio/{file}
- knowledge.py POST /knowledge/upload + GET /knowledge/list + DELETE /knowledge/{doc_id}
"""

from app.api.chat import router as chat_router
from app.api.knowledge import router as knowledge_router
from app.api.media import router as media_router

__all__ = ["chat_router", "knowledge_router", "media_router"]
