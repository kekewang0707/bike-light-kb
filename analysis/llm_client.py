"""LangChain ChatModel 工厂函数。

创建一个指向 DeepSeek API 的 ChatOpenAI 实例。
DeepSeek 提供 OpenAI 兼容的 API 端点，因此可以直接用 ChatOpenAI 接入。
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from config.settings import settings


def create_chat_model(
    temperature: float = 0.3,
    max_tokens: int = 2000,
    max_retries: int = 3,
    timeout: float = 60.0,
) -> ChatOpenAI:
    """创建指向 DeepSeek API 的 LangChain ChatModel。

    DeepSeek 暴露 OpenAI 兼容端点，因此用 ChatOpenAI 配合
    自定义 base_url 即可。同样支持其他兼容 provider（Qwen、GPT-4 等），
    只需修改环境变量中的 API Key。

    Args:
        temperature:  LLM 温度 (0.0-1.0)。提取任务推荐 0.3。
        max_tokens:   最大输出 token 数。
        max_retries:  网络级重试次数（内置指数退避）。
        timeout:      请求超时秒数。

    Returns:
        配置好的 ChatOpenAI 实例，支持 .invoke() / .ainvoke()。
    """
    return ChatOpenAI(
        base_url="https://api.deepseek.com/v1",
        api_key=settings.deepseek_api_key,
        model="deepseek-chat",
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=max_retries,
        timeout=timeout,
    )
