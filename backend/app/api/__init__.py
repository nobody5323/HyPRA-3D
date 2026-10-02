"""FastAPI 路由层。

- chat.py          POST /chat（完整对话链路：召回 + 生成 + 情绪 + 写入）+ 清单类接口
- avatar_models.py /media/avatar/models/*（数字人模型库的导入 / 指定情绪 / 删除 / 取文件）
- st_presets.py    /chat/st-presets/*（酒馆预设的导入 / 编辑 / 导出）
- studio.py        /chat/studio/*（用户自建角色卡 / 世界书条目的编辑）
- llm.py           /llm/*（对话模型的运行时切换，OpenAI 兼容）
- media.py         POST /media/avatar（数字人驱动数据）+ POST /media/speak + GET /media/audio/{file}
                   + GET /media/tts/voices（语音引擎与音色清单）
- knowledge.py     POST /knowledge/upload + GET /knowledge/list + DELETE /knowledge/{doc_id}
- plugins.py       /plugins/*（插件列表 / 启停 / 配置，以及酒馆会话→长期记忆的导入）
- skills.py        /skills/*（技能清单 / 详情 / 启停 / 删除与恢复 / 重扫，见 AGENTS.md §9.6）
- events.py        GET /events（SSE 推送通道：主动消息 / 感知提示，见 docs/proactive-multimodal.md §5.1）
- perception.py    POST /perception/asr（语音转写）+ POST /perception/desktop（桌面情景上报）
                   + GET /perception/status
- proactive.py     /proactive/*（主动链路状态 / 手动评估 / 重置节流）
"""

from app.api.avatar_models import router as avatar_models_router
from app.api.chat import router as chat_router
from app.api.events import router as events_router
from app.api.knowledge import router as knowledge_router
from app.api.llm import router as llm_router
from app.api.media import router as media_router
from app.api.perception import router as perception_router
from app.api.plugins import router as plugins_router
from app.api.proactive import router as proactive_router
from app.api.skills import router as skills_router
from app.api.st_presets import router as st_presets_router
from app.api.studio import router as studio_router

__all__ = [
    "avatar_models_router",
    "chat_router",
    "events_router",
    "knowledge_router",
    "llm_router",
    "media_router",
    "perception_router",
    "plugins_router",
    "proactive_router",
    "skills_router",
    "st_presets_router",
    "studio_router",
]
