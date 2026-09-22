"""ST 预设兼容层测试夹具（**全部自创**）。

合规说明（AGENTS.md §6）：这里只借用 SillyTavern 的**结构**（字段名与嵌套关系，
来自公开文档与官方默认预设的结构观察），所有 `content` 正文一律自写并标注为
测试语料，**不复制** SillyTavern 或任何社区预设的提示词原文。
"""

import pytest

from app.prompts.st_compat import STRenderContext
from app.session.context import ChatTurn

# ---- 测试语料（自写，仅用于验证解析/渲染行为）----
TEST_MAIN_TEXT = "（测试语料）请以角色身份回应对话者。"
TEST_JAILBREAK_TEXT = "（测试语料）保持体贴的语气，不要使用列表。"
TEST_REPLY_LENGTH_TEXT = "（测试语料）每次回复控制在三句话以内。"
TEST_INCHAT_TEXT = "（测试语料）用户此刻情绪偏低落，回应放慢一些。"


def make_chat_preset(**overrides) -> dict:
    """构造一份自创的 ST Chat Completion 预设（结构真实、正文自写）。

    overrides 会覆盖顶层字段，便于测试各种缺字段/异常字段的场景。
    """
    preset = {
        # ---- 采样与上下文 ----
        "temperature": 0.9,
        "top_p": 0.95,
        "frequency_penalty": 0.2,
        "presence_penalty": 0.1,
        "top_k": 40,
        "min_p": 0.05,
        "openai_max_context": 8192,
        "openai_max_tokens": 512,
        # ---- 组装控制（默认取中性值，便于断言逐条消息；测开关的用例显式开启）----
        "names_behavior": 0,
        "use_sysprompt": False,
        "squash_system_messages": False,
        # ---- 端点/密钥类字段（导入时应被剥离且不落盘）----
        "chat_completion_source": "openai",
        "openai_model": "gpt-test-model",
        "custom_url": "https://example.invalid/v1",
        "custom_include_headers": "Authorization: Bearer sk-test-header",
        "reverse_proxy": "https://proxy.invalid",
        "proxy_password": "sk-secret-value",
        "vendor_some_api_key": "sk-vendor-secret",
        # ---- 私有扩展字段（未知字段应原样保留）----
        "prompts": [
            {
                "identifier": "main",
                "name": "Main Prompt",
                "system_prompt": True,
                "role": "system",
                "content": TEST_MAIN_TEXT,
            },
            {"identifier": "worldInfoBefore", "name": "World Info (before)", "system_prompt": True, "marker": True},
            {"identifier": "charDescription", "name": "Char Description", "system_prompt": True, "marker": True},
            {"identifier": "charPersonality", "name": "Char Personality", "system_prompt": True, "marker": True},
            {"identifier": "scenario", "name": "Scenario", "system_prompt": True, "marker": True},
            {"identifier": "dialogueExamples", "name": "Chat Examples", "system_prompt": True, "marker": True},
            {"identifier": "chatHistory", "name": "Chat History", "system_prompt": True, "marker": True},
            {
                "identifier": "jailbreak",
                "name": "Post-History Instructions",
                "system_prompt": True,
                "role": "system",
                "content": TEST_JAILBREAK_TEXT,
            },
            {
                "identifier": "reply-length",
                "name": "回复长度",
                "role": "system",
                "content": TEST_REPLY_LENGTH_TEXT,
                "injection_position": 0,
            },
            {
                "identifier": "mood-note",
                "name": "情绪提醒",
                "role": "user",
                "content": TEST_INCHAT_TEXT,
                "injection_position": 1,
                "injection_depth": 2,
                "injection_order": 50,
            },
        ],
        "prompt_order": [
            {
                "character_id": 100000,
                "order": [
                    {"identifier": "main", "enabled": True},
                    {"identifier": "worldInfoBefore", "enabled": True},
                    {"identifier": "charDescription", "enabled": True},
                    {"identifier": "charPersonality", "enabled": True},
                    {"identifier": "scenario", "enabled": True},
                    {"identifier": "dialogueExamples", "enabled": True},
                    {"identifier": "chatHistory", "enabled": True},
                    {"identifier": "jailbreak", "enabled": True},
                    {"identifier": "reply-length", "enabled": False},   # 默认关闭，便于测开关
                    {"identifier": "mood-note", "enabled": True},
                ],
            }
        ],
    }
    preset.update(overrides)
    return preset


@pytest.fixture
def make_preset():
    """返回「构造自创测试预设」的工厂函数。"""
    return make_chat_preset


@pytest.fixture
def texts() -> dict[str, str]:
    """测试语料常量（经 fixture 暴露，避免测试文件之间互相 import）。"""
    return {
        "main": TEST_MAIN_TEXT,
        "jailbreak": TEST_JAILBREAK_TEXT,
        "reply_length": TEST_REPLY_LENGTH_TEXT,
        "in_chat": TEST_INCHAT_TEXT,
    }


@pytest.fixture
def make_context():
    """返回「构造渲染上下文」的工厂函数（每次重建可变字段，避免用例间串扰）。"""

    def _make(**overrides) -> STRenderContext:
        values = {
            "persona_text": "（测试语料）人设正文",
            "persona_personality": "（测试语料）性格标签",
            "scenario_text": "（测试语料）场景",
            "user_persona": "（测试语料）对话者描述",
            "worldbook_before": "（测试语料）世界书-前",
            "worldbook_after": "（测试语料）世界书-后",
            "history": [
                ChatTurn("user", "（测试语料）你好"),
                ChatTurn("assistant", "（测试语料）我在"),
            ],
            "examples": [("（测试语料）示例提问", "（测试语料）示例回应")],
            "user_input": "（测试语料）今天有点累",
            "memory_text": "（测试语料）记忆块",
            "persona_name": "苏澄",
            "user_name": "朋友",
        }
        values.update(overrides)
        return STRenderContext(**values)

    return _make
