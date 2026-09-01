"""LangChain ChatModel 工厂函数。

创建指向 OpenAI 兼容 API 的 ChatOpenAI 实例。
默认使用 DeepSeek API，通过参数覆盖可切换到其他兼容 provider
（如 DashScope Qwen-VL、GPT-4 等），无需重复编写工厂函数。

Usage::

    from analysis.llm_client import create_chat_model

    # 默认：DeepSeek（M3 评价分析）
    llm = create_chat_model()

    # DashScope Qwen-VL（M4 多模态图片描述）
    vl_llm = create_chat_model(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=settings.dashscope_api_key,
        model="qwen-vl-max",
    )
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from langchain_openai import ChatOpenAI

from config.settings import settings


def create_chat_model(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
    max_retries: int = 3,
    timeout: float = 60.0,
) -> Optional[ChatOpenAI]:
    """创建指向 OpenAI 兼容 API 的 LangChain ChatModel。

    默认连接 DeepSeek API。通过 base_url/api_key/model 参数
    可切换到其他兼容 provider（DashScope、GPT-4 等）。

    API key 未设置时返回 None（不抛异常），由调用方处理。

    Args:
        base_url:    API 端点。默认 DeepSeek: "https://api.deepseek.com/v1"。
        api_key:     API key。默认从 settings.deepseek_api_key 读取。
        model:       模型名。默认 "deepseek-chat"。
        temperature: LLM 温度 (0.0-1.0)。提取任务推荐 0.3。
        max_tokens:  最大输出 token 数。默认 2000 (DeepSeek) / 1000 (VL)。
        max_retries: 网络级重试次数（LangChain 内置指数退避）。
        timeout:     请求超时秒数。

    Returns:
        配置好的 ChatOpenAI 实例。API key 未设置时返回 None。
    """
    _base_url = base_url or "https://api.deepseek.com/v1"
    _api_key = api_key if api_key is not None else settings.deepseek_api_key
    _model = model or "deepseek-chat"
    _max_tokens = max_tokens if max_tokens is not None else 2000

    if not _api_key:
        logger.warning(
            f"API key 未设置 (model={_model})，ChatModel 不可用。"
            f"请检查对应的环境变量。"
        )
        return None

    return ChatOpenAI(
        base_url=_base_url,
        api_key=_api_key,
        model=_model,
        temperature=temperature,
        max_tokens=_max_tokens,
        max_retries=max_retries,
        timeout=timeout,
    )
