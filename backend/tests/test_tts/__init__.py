"""tts 子包测试：GPT-SoVITS 客户端 + 音频时长解析。

约束（与 tests/conftest.py 一致）：**不发起真实网络请求**——
GPT-SoVITS 用 httpx.MockTransport 注入，WAV 用标准库 `wave` 在内存里构造。
"""
