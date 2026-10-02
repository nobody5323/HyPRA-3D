"""内置插件注册：把既有扩展点收编进插件体系。

本模块是**唯一的**「业务代码 → 插件体系」粘合层（`AGENTS.md §9.1`）：其余模块不需要
知道插件体系的存在，因此这一步是**零重构**的——只做包装，不改任何既有实现。

被收编的 13 项（对应 §9.1 盘点表 + §9.11 P2）：

    llm-providers       → LLM provider 工厂（mock / dashscope / siliconflow / openai-compatible）
    avatar-providers    → 数字人驱动工厂（local / xmov / gpt_sovits）
    tts                 → 语音合成（gpt_sovits / none）—— §9.10 第 12 项：从 digital_human 分出
    embedding           → 文本向量化（deterministic / dashscope / siliconflow / openai-compatible）
    memory-warm         → 情景记忆存储（inmemory / qdrant）
    memory-knowledge    → 个人记忆存储（inmemory / qdrant）
    session-store       → 会话持久化（memory / sqlite）
    tools-builtin       → 4 个情感陪伴工具（情绪日记 / 趋势 / 呼吸 / 记忆检索）
    mcp-bridge          → MCP Client（外部工具接入）
    tokenizer           → 中文分词（jieba / char-bigram）—— P2 从模块级函数拆为可替换策略
    knowledge-parser    → 文档解析（text / md / pdf / docx）—— P2 同上
    knowledge-chunker   → 文档分块（semantic / plain）—— P2 同上
    preset-ai-adaptation → 预设适配动作（detect / plan / build_diff；按**动作名**寻址）
    asr                 → 语音识别（faster-whisper / none）—— 感知层，见 §4.3
    vision              → 图片理解（Qwen2.5-VL / none）—— 感知层，见 §4.4
    proactive           → 主动沟通（触发 / 节制闸门 / 复用对话链路）—— 见 §5

注：`proactive` 没有 provider 也没有 tool——它的能力是**编排**，
注册它只是为了拿到声明式配置表单（`AGENTS.md §9.5`），
让「免打扰时段 / 冷却 / 每日配额」这些闸门参数能在界面上调。

统一约定：provider 工厂签名为 `(name: str, **kwargs) -> 实现`，`name` 是**实现名**
（如 `"qdrant"`），由调用方透传；插件负责适配既有工厂的各自签名。

注：情绪兜底策略（§9.11 P2 的第三项）**刻意不收编到这里**——它属于 core 的
`emotion-pipeline`（关掉它情绪链路就不成立，见 §9.2），只在 core 内部抽了接口，
由 `EMOTION_FALLBACK` 配置选择实现。放进 builtin 会让它变成可禁用，语义不符。
"""

from __future__ import annotations

from typing import Any

from app.plugins.capabilities import CapabilityType, Permission, PluginLayer
from app.plugins.manifest import PluginManifest
from app.plugins.registry import PluginRegistration, PluginRegistry

#: 内置插件统一版本（与后端包版本对齐；后续由 CI 校验一致性）
_BUILTIN_VERSION = "0.1.0"


def _builtin_manifest(
    plugin_id: str,
    display_name: str,
    *,
    category: str,
    description: str,
    capabilities: list[CapabilityType],
    loading_order: int = 100,
    settings_schema: dict[str, Any] | None = None,
) -> PluginManifest:
    """构造内置插件的 manifest（无文件权限：内置实现只经宿主内存与配置工作）。"""
    return PluginManifest(
        id=plugin_id,
        display_name=display_name,
        version=_BUILTIN_VERSION,
        layer=PluginLayer.BUILTIN,
        category=category,
        description=description,
        capabilities=capabilities,
        loading_order=loading_order,
        permissions=Permission(),
        settings_schema=settings_schema or {},
    )


# =============================================================
# 声明式配置：settings_schema
# =============================================================
#
# **键名刻意与 Settings 字段同名**（`embedding_provider` ↔ `EMBEDDING_PROVIDER`）：
# 宿主按同名规则把它作为运行时覆盖（界面设置 > .env，见 app/config.py 的
# RUNTIME_OVERRIDABLE_FIELDS），于是 .env.example、界面字段、白名单三者一一对应，
# 用户不必学第二套命名。不同名的键（如 tavern-bridge 的 `tavern_dir`）就只是
# 插件自己的配置，不参与覆盖。
#
# **留空 = 沿用 .env**：前端清空某项就是删键，收集时自然不计入覆盖。
# 所以这里**不写 default**——表单的初始值由宿主用「当前生效值」回填
# （GET /plugins/{id}/settings），另写一份默认值只会与 .env 双份漂移。

_EMBEDDING_SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "embedding_provider": {
            "type": "string",
            "enum": ["deterministic", "dashscope", "siliconflow", "openai-compatible"],
            "title": "向量化服务",
            "description": (
                "deterministic = 零依赖本地实现（默认，可离线跑）：注意它的相似度分布"
                "与云端完全不同（实测 0.0–0.2 对 0.4–0.9），世界书的向量触发阈值需按它另调。"
            ),
        },
        "embedding_api_key": {
            "type": "string",
            "format": "password",
            "title": "API Key",
            "description": "云端向量化服务必填。只存本机后端（已 gitignore），接口不回传明文。",
        },
        "embedding_model": {
            "type": "string",
            "title": "模型",
            "description": "如 text-embedding-v3 / BAAI/bge-m3 / Qwen/Qwen3-Embedding-8B。",
        },
        "embedding_base_url": {
            "type": "string",
            "title": "Base URL（端点）",
            "description": (
                "留空 = 用该服务商的默认端点（dashscope / siliconflow 均有默认）；"
                "openai-compatible 必填。"
            ),
        },
        "embedding_dim": {
            "type": "integer",
            "minimum": 1,
            "title": "向量维度",
            "description": (
                "必须与模型实际输出一致（Qdrant 建 collection 要静态维度）："
                "text-embedding-v3 = 1024，Qwen3-Embedding-8B = 4096。"
                "**改维度需重建向量库 collection**，否则写入会报维度不符。"
            ),
        },
        "embedding_timeout": {
            "type": "number",
            "minimum": 1,
            "title": "超时（秒）",
            "description": "单次请求超时。知识库批量编码时容易偏慢，可适当放大。",
        },
    },
}

_TTS_SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "gpt_sovits_base_url": {
            "type": "string",
            "title": "服务地址",
            "description": "GPT-SoVITS 服务根地址（默认 http://127.0.0.1:9880）。",
        },
        "gpt_sovits_ref_audio": {
            "type": "string",
            "title": "参考音频路径",
            "description": (
                "音色 = 参考音频 + 它对应的文字（零样本克隆）。填**跑 GPT-SoVITS 那台机器**"
                "上可见的路径，不是本机相对路径；音频长度需 3~10 秒。"
            ),
        },
        "gpt_sovits_prompt_text": {
            "type": "string",
            "title": "参考文本",
            "description": "参考音频对应的文字。留空也能合成（速度更快、相似度略降）。",
        },
        "gpt_sovits_prompt_lang": {
            "type": "string",
            "title": "参考文本语言",
            "description": "如 zh / ja / en / ko / yue / auto，默认 zh。",
        },
        "gpt_sovits_text_lang": {
            "type": "string",
            "title": "合成文本语言",
            "description": "待合成文本的语言，默认 zh。",
        },
        "gpt_sovits_media_type": {
            "type": "string",
            "enum": ["wav", "ogg", "aac"],
            "title": "音频格式",
            "description": (
                "wav 浏览器可直接播放（推荐）。刻意不列 raw：那是裸 PCM，`<audio>` 放不了。"
            ),
        },
        "gpt_sovits_speed": {
            "type": "number",
            "minimum": 0.1,
            "title": "语速",
            "description": "1.0 = 原速。",
        },
        "gpt_sovits_timeout": {
            "type": "number",
            "minimum": 1,
            "title": "超时（秒）",
            "description": "单次合成请求超时。首次请求要加载模型，可适当放大。",
        },
        "gpt_sovits_extra_params": {
            "type": "string",
            "title": "额外请求参数（JSON）",
            "description": (
                '一行 JSON，原样透传给服务端的 /tts，用于按需调优，如 {"parallel_infer": false}。'
                "核心字段（text / ref_audio_path / media_type…）不可覆盖，会被忽略。"
            ),
        },
        "gpt_sovits_voices_file": {
            "type": "string",
            "title": "音色表文件",
            "description": (
                "JSON 音色表（键 = 音色 id，前端 voice 字段传它）；相对路径按数据目录解析。"
                "文件不存在 = 只用上面的默认音色；改动后重启后端生效。"
            ),
        },
        "gpt_sovits_default_voice": {
            "type": "string",
            "title": "默认音色 id",
            "description": "未指定音色时用哪一个；留空 = 用上面的默认参考音频。",
        },
        "gpt_sovits_warmup": {
            "type": "boolean",
            "title": "启动时预热",
            "description": "后端启动后后台预热一次模型，首个请求不偏慢。改动需重启后端生效。",
        },
    },
}


# =============================================================
# 感知层与主动链路的声明式配置
# （设计见 docs/proactive-multimodal.md §4 / §5）
# =============================================================
#
# 键名同样与 Settings 字段同名（见上方说明），因此界面填完保存即生效、
# 不必改 .env、不必重启。

_ASR_SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "asr_provider": {
            "type": "string",
            "enum": ["none", "faster-whisper"],
            "title": "语音识别",
            "description": (
                "none = 关闭（界面不出现麦克风）；faster-whisper = 本地识别"
                "（需先在后端环境执行 pip install faster-whisper）。"
                "**这个字段本身就是开关**——不另设 ASR_ENABLED，否则会出现"
                "「开关开着但 provider=none」这种自相矛盾的状态。"
            ),
        },
        "asr_model": {
            "type": "string",
            "enum": ["tiny", "base", "small", "medium", "large-v3"],
            "title": "模型规格",
            "description": (
                "越大越准也越慢。small 是「中文日常对话 + CPU 可用」的平衡点；"
                "模型权重需自备（见下一项），不随发布包分发。"
            ),
        },
        "asr_model_dir": {
            "type": "string",
            "title": "模型权重目录",
            "description": (
                "相对路径按数据目录解析。该目录下应有 <规格>/model.bin 等文件"
                "（如 small/model.bin）。**模型不进发布包**，需用户自备。"
            ),
        },
        "asr_device": {
            "type": "string",
            "enum": ["cpu", "cuda"],
            "title": "推理设备",
            "description": "cpu 通用；有 N 卡可改 cuda（需装对应版本的 CUDA 运行库）。",
        },
        "asr_compute_type": {
            "type": "string",
            "title": "计算精度",
            "description": "CPU 推荐 int8；CUDA 可换 float16。",
        },
        "asr_language": {
            "type": "string",
            "title": "语言",
            "description": "如 zh / en；留空 = 自动检测（检测会多花一点时间）。",
        },
        "asr_beam_size": {
            "type": "integer",
            "minimum": 1,
            "title": "束搜索宽度",
            "description": "越大越准也越慢，5 是常用值。",
        },
        "asr_warmup": {
            "type": "boolean",
            "title": "启动时预热",
            "description": "后端启动后后台加载模型，第一次说话不必等十几秒。改动需重启后端生效。",
        },
        "asr_vad_filter": {
            "type": "boolean",
            "title": "语音活动检测（VAD）",
            "description": (
                "**建议保持开启**。Whisper 在没人声的音频上会吐出训练集水印"
                "（实测 small 模型 + 纯音 → 「字幕by索兰娅」），"
                "而这句会被当成用户说的话填进输入框。VAD 从源头切掉非语音段。"
            ),
        },
        "asr_no_speech_threshold": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "title": "无语音判定阈值",
            "description": "越大越容易判成「没人说话」。默认 0.6；误识别多时可上调。",
        },
        "asr_ui_enabled": {
            "type": "boolean",
            "title": "在对话界面显示语音按钮",
            "description": (
                "关掉后**能力仍在**（接口照常可用），只是不再渲染那个按钮"
                "——给「用快捷键说话、不想看见按钮」的人留的开关。"
            ),
        },
        "asr_shortcut": {
            "type": "string",
            "title": "语音快捷键",
            "description": (
                "形如 `Ctrl+Shift+M`（空 = 不启用）。按一下开始录音，再按一下结束，"
                "等价于点按钮；识别结果同样只填进输入框，不会自动发送。"
                "**只在窗口获得焦点时生效**——不注册系统级全局热键，"
                "那会与你的其它软件抢键。"
            ),
        },
    },
}

# 注：`perception_desktop_enabled` / `perception_include_window_title` 与
# 时间 / 行踪 / 画像同属「环境感知」，已统一挪到下面的 `_AMBIENT_SETTINGS_SCHEMA`。
# 原先它们挂在「语音识别」插件的表单里——那是历史遗留（两者同属 §4 的感知层），
# 而用户不会想到「桌面情景开关」要去「语音识别」里找。

_AMBIENT_SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "perception_desktop_enabled": {
            "type": "boolean",
            "title": "启用桌面情景",
            "description": (
                "是否接收桌宠端上报的「正在听的歌 / 前台窗口 / 空闲 / 电量 / 时段」。"
                "**这是行踪与所有情景类主动开口的数据来源**——"
                "关掉后采集链路立即停止，已有数据（含行踪）也会被清掉。"
            ),
        },
        "perception_include_window_title": {
            "type": "boolean",
            "title": "把前台窗口标题也告诉角色",
            "description": (
                "**默认关**。歌名是用户主动播放的、可以聊；窗口标题可能是一封邮件"
                "或一个病历页面。窗口的**进程名**始终会采集（忙碌判定与行踪需要），"
                "但不会进提示词。"
            ),
        },
        "perception_include_time": {
            "type": "boolean",
            "title": "让角色知道现在几点",
            "description": (
                "把「日期 / 星期几 / 什么时段」告诉角色。**建议保持开启**："
                "没有它，模型会在凌晨两点说「今天过得怎么样呀」——"
                "那不是人设的问题，是它真的不知道时间。"
            ),
        },
        "perception_activity_enabled": {
            "type": "boolean",
            "title": "记录行踪（最近在电脑上做什么）",
            "description": (
                "记录「几点到几点在用什么程序」，用于主动开口的由头"
                "（「忙完了？」「回来啦」）。**默认只记程序名，不记窗口标题**；"
                "记录保留有限天数，且可随时在桌宠设置里一键清除。"
                "关掉后采集立即停止，已记录的内容也会被清掉。"
            ),
        },
        "perception_activity_recent_hours": {
            "type": "number",
            "minimum": 0.5,
            "title": "「最近在做什么」回看几小时",
            "description": "默认 3。够覆盖「这一段在忙什么」，又不会把上午的事说成「最近」。",
        },
        "perception_activity_retention_days": {
            "type": "integer",
            "minimum": 1,
            "title": "行踪保留天数",
            "description": (
                "**刻意短**：行踪是敏感数据，够用就好——留得越久越像在攒档案。默认 7 天。"
            ),
        },
        "perception_activity_include_title": {
            "type": "boolean",
            "title": "行踪里连窗口标题一起记",
            "description": (
                "**默认关**。标题是内容（可能是一份体检报告、一封邮件），"
                "程序名才是类别。开启后行踪会精确到「在写哪个文档」，"
                "但被记录的内容也随之敏感很多。"
            ),
        },
        "perception_activity_idle_gap_seconds": {
            "type": "number",
            "minimum": 30,
            "title": "多久没操作算「人不在」（秒）",
            "description": (
                "超过它就把当前这段行踪收尾。默认 300 秒：接个电话、去倒杯水不会断开，"
                "真的离开一定会断开。**调太大会记出「连续几小时在用同一个软件」**"
                "——那正是行踪最不能出的错。"
            ),
        },
        "perception_activity_max_segment_hours": {
            "type": "number",
            "minimum": 0.5,
            "title": "同一程序最长算一段（小时）",
            "description": "默认 4。超过就断开，让「上午 / 下午」在行踪里可分辨。",
        },
        "perception_profile_enabled": {
            "type": "boolean",
            "title": "总结主人的偏好（常用程序 / 活跃时段）",
            "description": (
                "从行踪里聚合出「最近几天最常用什么、什么时候活跃」。"
                "**是计数不是推断**——每一句都能追到具体记录，不花模型调用，"
                "也不会写出「他工作压力很大」这种越界的结论。"
                "**依赖上面的「记录行踪」**：行踪关掉时这一项自动失效"
                "（画像没有别的数据来源）。"
            ),
        },
        "perception_profile_days": {
            "type": "integer",
            "minimum": 1,
            "title": "偏好回看天数",
            "description": "默认 7 天。",
        },
        "perception_profile_min_minutes": {
            "type": "number",
            "minimum": 0,
            "title": "样本下限（分钟）",
            "description": (
                "累计记录不足这么多分钟就不出偏好——"
                "用两分钟的行踪说「他最常用 Code.exe」是不诚实的。默认 30 分钟。"
            ),
        },
    },
}

_PROACTIVE_SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proactive_enabled": {
            "type": "boolean",
            "title": "主动开口",
            "description": (
                "默认开，但**默认就是克制的**：下面的免打扰 / 冷却 / 配额 / "
                "对话中不插话四条同时生效。关掉即全停，一键生效。"
            ),
        },
        "proactive_quiet_hours": {
            "type": "string",
            "title": "免打扰时段",
            "description": (
                "格式 HH:MM-HH:MM，支持跨零点（如 23:00-08:00）。"
                "**关怀型触发**（深夜还在活动 / 电量告急）可在用户清醒时豁免。"
            ),
        },
        "proactive_quiet_exempt_care": {
            "type": "boolean",
            "title": "允许关怀型豁免免打扰",
            "description": (
                "免打扰的目的是「别吵醒睡着的人」，不是「看着你熬夜也不说话」。"
                "豁免的前提是**用户此刻清醒**（刚还在操作电脑），不是「现在几点」。"
            ),
        },
        "proactive_min_interval_minutes": {
            "type": "integer",
            "minimum": 1,
            "title": "最小间隔（分钟）",
            "description": (
                "两条主动消息之间的冷却时间。默认 30——仍能保证「不会连着两句」；"
                "想要更安静就调大（如 90），想要更活跃可调小。"
            ),
        },
        "proactive_daily_quota": {
            "type": "integer",
            "minimum": 0,
            "title": "每日上限（条）",
            "description": (
                "一天最多主动开口几次。默认 12（约清醒时段每 1~1.5 小时一次）。"
                "0 = 等于关闭。真正防轰炸的是「最小间隔」与「对话中不插话」，不是这个上限。"
            ),
        },
        "proactive_daily_time": {
            "type": "string",
            "title": "每日问候时刻",
            "description": "每天这个时刻主动打个招呼（HH:MM）。留空 = 关闭该触发器。",
        },
        "proactive_idle_hours": {
            "type": "number",
            "title": "静默多久问一句（小时）",
            "description": "用户超过这么久没说话就问一句。默认 3。<= 0 = 关闭该触发器。",
        },
        "proactive_memory_followup_days": {
            "type": "number",
            "title": "搁置多久回头问（天）",
            "description": (
                "用户提过的**进行中事项**搁置超过这么多天就回头问一句"
                "（「你上次说在准备面试，后来怎么样了？」）。<= 0 = 关闭该触发器。"
                "**同一件事只问一次**——问两遍是唠叨。"
            ),
        },
        "proactive_reply_char_limit": {
            "type": "integer",
            "minimum": 10,
            "title": "主动开口字数上限",
            "description": "比常规回复更短——搭话不该是一段独白。",
        },
        "proactive_back_from_away_minutes": {
            "type": "number",
            "title": "离开多久算「久别」（分钟）",
            "description": (
                "用户离开超过这么久之后回到电脑前，可以自然地打个招呼（「回来啦」）。"
                "默认 30——够区分「去倒杯水」与「出门了一趟」。<= 0 = 关闭该触发器。"
                "**依赖「行踪」**：行踪关掉时它自动失效。"
            ),
        },
        "proactive_activity_shift_minutes": {
            "type": "number",
            "title": "「忙完了」判定时长（分钟）",
            "description": (
                "从写代码 / 文档 / 设计切到游戏 / 看片，且新的一段已经持续这么久，"
                "就问一句「忙完了？」。默认 20——短于它可能只是切过去看一眼。"
                "<= 0 = 关闭该触发器。**只做「做事→放松」这一个方向**："
                "反方向正是用户要进入状态的时候，开口就是打断。"
            ),
        },
    },
}


_VISION_SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "vision_provider": {
            "type": "string",
            "enum": ["none", "openai-compatible", "dashscope", "siliconflow"],
            "title": "图片理解",
            "description": (
                "none = 关闭（界面不出现「发图」入口）；其余为 Qwen2.5-VL，"
                "走 OpenAI 兼容端点——**零新依赖**（openai SDK 已在本项目里）。"
                "dashscope / siliconflow 会自动取该家的默认端点，只填 Key 即可。"
            ),
        },
        "vision_api_key": {
            "type": "string",
            "format": "password",
            "title": "API Key",
            "description": "与对话模型可以是同一把 key（阿里百炼 / 硅基流动通用）。只存本机后端，接口不回传明文。",
        },
        "vision_model": {
            "type": "string",
            "title": "模型",
            "description": (
                "如 qwen2.5-vl-72b-instruct（百炼）/ Qwen/Qwen2.5-VL-72B-Instruct（硅基流动）。"
                "**视觉模型与对话模型分开配**：绑在一起的话，没配 VL 模型就连聊天都用不了。"
            ),
        },
        "vision_base_url": {
            "type": "string",
            "title": "Base URL（端点）",
            "description": "留空 = 按上面的 provider 取默认端点；openai-compatible 需显式填。",
        },
        "vision_timeout": {
            "type": "number",
            "minimum": 1,
            "title": "超时（秒）",
            "description": "单张图片的理解超时。大图 + 大模型会偏慢，可适当放大。",
        },
        "vision_max_image_mb": {
            "type": "number",
            "minimum": 0.1,
            "title": "图片大小上限（MB）",
            "description": "图片以 data URL 内联进请求、**不落盘**；上限防止一次传进过大的图。",
        },
        "vision_prompt": {
            "type": "string",
            "title": "描述指令",
            "description": (
                "留空 = 用内置的「只描述看得见的」客观描述指令。"
                "**不建议改成让模型推断情绪**——推断是人设的职责，"
                "感知层只该提供素材。"
            ),
        },
        "vision_ui_enabled": {
            "type": "boolean",
            "title": "在对话界面显示「图片」按钮",
            "description": "关掉后接口仍可用，只是不再渲染入口。",
        },
    },
}


# =============================================================
# 适配器：把既有工厂各自不同的签名统一为 (name, **kwargs) -> 实现
# 用函数内 import：避免插件框架在导入期反向依赖业务模块，也加快宿主启动。
# =============================================================


def _llm_provider(name: str, **kwargs: Any) -> Any:
    from app.llm.factory import create_llm_provider

    return create_llm_provider(name or "mock", **kwargs)


def _digital_human_provider(name: str, **kwargs: Any) -> Any:
    from app.digital_human.factory import create_digital_human_provider

    return create_digital_human_provider(name or "local", **kwargs)


def _tts_provider(name: str, **kwargs: Any) -> Any:
    """按实现名取语音合成；空则 none（不做合成，前端回落浏览器 TTS）。"""
    from app.tts.factory import create_tts_provider

    return create_tts_provider(name, **kwargs)


def _warm_store(name: str, **kwargs: Any) -> Any:
    from app.memory.warm.factory import create_warm_store

    return create_warm_store(name or "memory", **kwargs)


def _embedding_provider(name: str, **kwargs: Any) -> Any:
    """按实现名取文本向量化 provider；空则用零依赖的确定性实现。

    注意既有工厂的首个参数叫 `provider` 而不是 `name`——适配器的职责之一就是
    抹平这类命名差异（与 `_session_store` 抹平 `db_path` 位置参数同理）。
    """
    from app.memory.warm.embedding import create_embedding_provider

    return create_embedding_provider(name, **kwargs)


def _knowledge_store(name: str, **kwargs: Any) -> Any:
    from app.memory.knowledge.factory import create_knowledge_store

    return create_knowledge_store(name or "memory", **kwargs)


def _session_store(name: str, **kwargs: Any) -> Any:
    from app.session.factory import create_session_store

    # 既有签名的 db_path 是位置参数（与其它工厂不同），在此对齐
    return create_session_store(name or "memory", kwargs.get("db_path", ""))


def _mcp_manager(name: str, **kwargs: Any) -> Any:  # noqa: ARG001 - 保留统一签名
    from app.mcp.manager import McpManager

    return McpManager(**kwargs)


def _builtin_tools() -> list[Any]:
    """既有内置工具作为 `ToolSpec` 列表（供插件注册表聚合）。"""
    from app.tools.builtin_tools import build_default_registry

    registry = build_default_registry()
    return [registry.get(name) for name in registry.names()]


def _tokenizer(name: str, **kwargs: Any) -> Any:  # noqa: ARG001 - 保留统一签名
    """按实现名取分词器；空则走 auto（有 jieba 用 jieba，否则降级 bigram）。"""
    from app.rag.retrieval.tokenize.factory import create_tokenizer

    return create_tokenizer(name or "auto")


def _parser(name: str, **kwargs: Any) -> Any:  # noqa: ARG001 - 保留统一签名
    """按**格式名**取文档解析器；name 为空时返回按文件名自动选的 `parse` 入口。

    parser 与其余 provider 不同：它不「按名创建」而是**按上传文件的扩展名**选，
    所以这里把能力面开成两层——`provider("parser", "pdf")` 取具体实现，
    `provider("parser")` 取自动选择的入口。
    """
    from app.memory.knowledge.parser import factory

    if not name:
        return factory.parse
    for parser in factory.PARSERS:
        if parser.name == name:
            return parser
    known = " | ".join(parser.name for parser in factory.PARSERS)
    raise ValueError(f"未知文档格式：{name!r}（可选 {known}）")


def _chunker(name: str, **kwargs: Any) -> Any:
    """按实现名取分块器；空则 auto（给了 embed 用语义边界，否则纯递归）。"""
    from app.memory.knowledge.chunker.factory import create_chunker

    return create_chunker(name, **kwargs)


def _preset_adaptation(name: str, **kwargs: Any) -> Any:  # noqa: ARG001 - 保留统一签名
    """按**动作名**取预设适配能力。

    与其他 provider 不同，这里的 `name` 不是「实现名」而是「动作名」——
    预设适配天然是**一组动作**（体检 / 规划 / 差异 / 载规则），而不是若干可换的实现；
    硬造一个只有一个实现的策略接口，只是把“包装”写成“架构”。
    空名返回整组，便于调用方一次取走。
    """
    from app.prompts import adaptation

    actions: dict[str, Any] = {
        "detect": adaptation.detect,
        "plan": adaptation.plan,
        "build_diff": adaptation.build_diff,
        "load_rules": adaptation.load_rules,
        "load_rules_file": adaptation.load_rules_file,
    }
    key = (name or "").strip()
    if not key:
        return actions
    if key not in actions:
        raise ValueError(f"未知适配动作：{name!r}（可选 {' | '.join(actions)}）")
    return actions[key]


def _asr_provider(name: str, **kwargs: Any) -> Any:
    """按实现名取语音识别 provider；空名或未知名回落 `none`（不报错）。

    与其它 provider 的差别：这里**不抛异常**。ASR 是可选能力，
    配置写错只该让麦克风消失（界面上看得见），不该让后端起不来。
    """
    from app.perception.asr import create_asr_provider

    return create_asr_provider(name, **kwargs)


def _vision_provider(name: str, **kwargs: Any) -> Any:
    """按实现名取视觉 provider；空名或未知名回落 `none`（不报错，同 ASR）。"""
    from app.perception.vision import create_vision_provider

    return create_vision_provider(name, **kwargs)


# =============================================================
# 注册入口
# =============================================================


def build_builtin_registrations() -> list[PluginRegistration]:
    """构造全部内置插件的注册对象（不写入注册表，便于测试检查）。"""
    return [
        PluginRegistration(
            manifest=_builtin_manifest(
                "llm-providers",
                "对话模型接入",
                category="provider",
                description=(
                    "LLM provider 工厂：mock（零依赖占位）/ dashscope（阿里百炼）"
                    "/ siliconflow（硅基流动）/ openai-compatible（任意兼容端点）"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=100,
            ),
            providers={"llm": _llm_provider},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "avatar-providers",
                "数字人驱动",
                category="provider",
                description=(
                    "具身驱动 provider 工厂：local（零依赖降级）/ xmov（魔珐星云）"
                    "/ gpt_sovits（自部署 TTS）"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=110,
            ),
            providers={"digital_human": _digital_human_provider},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "tts",
                "语音合成",
                category="media",
                description=(
                    "文本转语音：gpt_sovits（自部署服务）/ none（不做合成，前端回落浏览器 TTS）；"
                    "音频交给数字人驱动组装口型时间轴。**是否启用服务端语音由"
                    " DIGITAL_HUMAN_PROVIDER=gpt_sovits 决定**（不另设第二个开关，"
                    "那两个配置项迟早会互相打架）；本插件配的是服务地址与音色等参数。"
                ),
                capabilities=[CapabilityType.PROVIDER],
                # 紧跟 avatar-providers：数字人驱动会经它取音频
                loading_order=112,
                settings_schema=_TTS_SETTINGS_SCHEMA,
            ),
            providers={"tts": _tts_provider},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "embedding",
                "文本向量化",
                category="memory",
                description=(
                    "把文本编码为向量：deterministic（零依赖本地，默认）/ dashscope "
                    "/ siliconflow / openai-compatible；温层召回与知识库共用同一实现。"
                    "在这个详情里可直配 API（界面设置优先于 .env）"
                ),
                capabilities=[CapabilityType.PROVIDER],
                # 排在 memory-warm（120）之前：温层与知识库的向量都靠它
                loading_order=115,
                settings_schema=_EMBEDDING_SETTINGS_SCHEMA,
            ),
            providers={"embedding": _embedding_provider},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "memory-warm",
                "情景记忆存储",
                category="memory",
                description="温层向量存储：memory（零依赖内存）/ qdrant（本地嵌入式或云端）",
                capabilities=[CapabilityType.PROVIDER],
                loading_order=120,
            ),
            providers={"warm_store": _warm_store},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "memory-knowledge",
                "个人记忆存储",
                category="memory",
                description="知识库（用户上传语料）存储：memory / qdrant",
                capabilities=[CapabilityType.PROVIDER],
                loading_order=130,
            ),
            providers={"knowledge_store": _knowledge_store},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "session-store",
                "会话持久化",
                category="memory",
                description="会话与滚动窗口的存储后端：memory（进程内）/ sqlite（落库）",
                capabilities=[CapabilityType.PROVIDER],
                loading_order=140,
            ),
            providers={"session_store": _session_store},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "tools-builtin",
                "内置陪伴工具",
                category="tool",
                description=(
                    "4 个情感陪伴工具：情绪日记 / 情绪趋势 / 呼吸练习 / 记忆检索"
                    "（模型通过 function calling 自主调用）"
                ),
                capabilities=[CapabilityType.TOOL],
                loading_order=150,
            ),
            tools=_builtin_tools(),
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "mcp-bridge",
                "MCP 外部工具接入",
                category="integration",
                description="MCP Client：连接外部 MCP server，把工具接进 Agent 行动层",
                capabilities=[CapabilityType.TOOL],
                loading_order=160,
            ),
            providers={"mcp_manager": _mcp_manager},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "tokenizer",
                "中文分词",
                category="retrieval",
                description=(
                    "BM25 稀疏检索的分词器：jieba（主）/ char-bigram（零依赖降级）；"
                    "由 TOKENIZER_BACKEND 选择"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=170,
            ),
            providers={"tokenizer": _tokenizer},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "knowledge-parser",
                "文档解析",
                category="retrieval",
                description=(
                    "个人记忆的上传解析：text / md / pdf / docx；"
                    "pdf 与 docx 的可选依赖惰性导入，缺失只影响该格式"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=180,
            ),
            providers={"parser": _parser},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "knowledge-chunker",
                "文档分块",
                category="retrieval",
                description=(
                    "把长文本切成入库与召回的分块：semantic（语义边界）/ plain（纯递归，可复现）；"
                    "长度硬约束与 overlap 由共用骨架保证"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=190,
            ),
            providers={"chunker": _chunker},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "preset-ai-adaptation",
                "预设 AI 适配",
                category="prompt",
                description=(
                    "导入酒馆预设后的一键适配：规则层确定性体检与修复（detect / build_diff）"
                    "+ 规划层攒成一次模型改写（plan）"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=195,
            ),
            providers={"preset_adaptation": _preset_adaptation},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "asr",
                "语音识别",
                category="perception",
                description=(
                    "把录音转成文本（本地 faster-whisper / none 关闭）。"
                    "结果**只转写、不自动发送**——回填输入框由用户确认后再发。"
                    "模型权重不进发布包，由用户自备。"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=200,
                settings_schema=_ASR_SETTINGS_SCHEMA,
            ),
            providers={"asr": _asr_provider},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "vision",
                "图片理解",
                category="perception",
                description=(
                    "把用户分享的图片读成中性事实描述（Qwen2.5-VL，走 OpenAI 兼容端点，零新依赖）。"
                    "**只做用户主动分享**——摄像头与连续屏幕感知已明确否决；"
                    "图片以 data URL 内联、不落盘。"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=202,
                settings_schema=_VISION_SETTINGS_SCHEMA,
            ),
            providers={"vision": _vision_provider},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "perception-ambient",
                "时间 · 行踪 · 画像",
                category="perception",
                description=(
                    "让角色知道「现在几点」「你最近在电脑上做什么」「你平时是什么节奏」："
                    "服务端时钟（Web 端也有）+ 行踪（按段记录，只记程序名，默认保留 7 天、"
                    "可一键清除）+ 偏好画像（从行踪确定性聚合，不调模型）。"
                    "三样都注入提示词的「此刻」层，并成为主动开口的由头。"
                ),
                # 无 provider 也无 tool：它的能力是**采集与聚合**，
                # 注册它是为了拿到声明式配置表单（§9.5），
                # 让「记多久 / 记不记标题 / 要不要画像」这些隐私选择在界面上可见可改。
                capabilities=[CapabilityType.SETTINGS],
                # 紧跟 vision（202）：同属感知层，且要先于 proactive（205）——
                # 主动链路的两个新触发器消费的正是这一层的输出
                loading_order=203,
                settings_schema=_AMBIENT_SETTINGS_SCHEMA,
            ),
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "proactive",
                "主动沟通",
                category="perception",
                description=(
                    "让角色自己开口：触发（定时 / 间隔 / 情景）→ 七道节制闸门 → "
                    "复用同一条对话链路生成 → SSE 推送到界面。"
                    "默认开，但免打扰 / 冷却 / 每日配额 / 对话中不插话同时默认生效。"
                ),
                # 无 provider 也无 tool：它的能力是**编排**（见 §9.2 的 core 说明），
                # 这里注册它是为了拿到声明式配置表单（§9.5）。
                capabilities=[CapabilityType.SETTINGS],
                loading_order=205,
                settings_schema=_PROACTIVE_SETTINGS_SCHEMA,
            ),
        ),
    ]


def register_all_builtin(registry: PluginRegistry) -> list[str]:
    """把内置插件注册进注册表；返回注册的插件 id 列表。"""
    registrations = build_builtin_registrations()
    for registration in registrations:
        registry.register(registration)
    return [r.manifest.id for r in registrations]
