"""tts 子包：文本 → 语音（音频字节）。

与 `app/digital_human/` 的分工：
- 本子包只负责**拿到音频**（各家 TTS 服务客户端）；
- `app/digital_human/` 负责把音频 + 情绪组装成**驱动时间轴**（口型 / 表情 / 动作）。

当前实现：
- gpt_sovits.py  GPT-SoVITS（官方 api_v2.py）客户端
- audio.py      音频时长解析（零依赖，只用标准库）

为什么把 TTS 从数字人驱动里拆出来：
魔珐星云自带 TTS（`app/digital_human/xmov_tts.py`，且能返回字级时间戳），
而 GPT-SoVITS 是**独立服务**、且不返回字级时间戳——两者的能力不同，
用一层薄客户端隔离「怎么取音频」，驱动层就只需关心「拿到音频后怎么对齐口型」。
"""
