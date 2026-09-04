"""多模态图片描述器 — Qwen-VL via LangChain。

使用 LangChain ChatOpenAI 接入 DashScope Qwen-VL 多模态模型，
对自行车灯商品图片生成综合描述。

ChatModel 由 analysis.llm_client.create_chat_model() 统一创建，
通过参数覆盖切换到 DashScope 端点（与 M3 共用同一个工厂函数）。

技术路线（与 M3 ReviewAnalyzer 一致）：
- ChatOpenAI 指向 DashScope OpenAI 兼容端点
- with_structured_output(method="function_calling") 绑定 ImageDescription schema
- function calling 失败时回退 json_mode（_fallback_invoke）
- 重试由 LangChain 内置 max_retries 处理

Usage::

    from analysis.image.multimodal import MultimodalDescriber
    from analysis.llm_client import create_chat_model

    # 方式 1：直接使用（自动创建 VL ChatModel）
    describer = MultimodalDescriber()
    desc = await describer.describe("data/images/1/main_1.jpg")

    # 方式 2：自定义模型
    llm = create_chat_model(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="...",
        model="qwen-vl-max",
    )
    describer = MultimodalDescriber(llm=llm)
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import List, Optional

from loguru import logger
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.exceptions import OutputParserException

from config.settings import settings
from analysis.llm_client import create_chat_model


# ============================================================================
# 数据模型（Pydantic — 供 with_structured_output 使用）
# ============================================================================

class ImageDescription(BaseModel):
    """图片的多模态语义描述。

    字段与 M3 的 ReviewExtraction 模式一致：
    使用 Pydantic BaseModel + Field(description=...)。
    """

    appearance: str = Field(
        default="",
        description="产品外观描述，包括形状、颜色、大小、灯珠数量、显示屏等",
    )
    mount_type: str = Field(
        default="",
        description="安装方式：车把夹装/头盔/座管/绑带式/未知",
    )
    scene: str = Field(
        default="",
        description="展示场景：白底棚拍/户外骑行/夜拍光效/功能对比图/其他",
    )
    style: str = Field(
        default="",
        description="视觉风格：极简/运动/科技感/复古/户外硬核/其他",
    )
    text_overlay: str = Field(
        default="",
        description="画面中的文字信息，如卖点标签、规格标注等",
    )


# ============================================================================
# Prompt
# ============================================================================

DESCRIPTION_PROMPT = """描述这张自行车灯商品主图：

1. 产品外观（形状/颜色/大小/灯珠数量/显示屏）
2. 安装方式（车把夹装/头盔/座管/绑带式）
3. 展示场景（白底棚拍/户外骑行/夜拍光效/功能对比图）
4. 视觉风格（极简/运动/科技感/复古/户外硬核）
5. 画面中的文字信息（卖点标签、规格标注）"""


# ============================================================================
# 多模态描述器
# ============================================================================

class MultimodalDescriber:
    """多模态图片语义描述器。

    使用 Qwen-VL（通过 LangChain ChatOpenAI → DashScope）生成图片描述。
    ChatModel 由 analysis.llm_client.create_chat_model() 统一创建。
    架构与 M3 ReviewAnalyzer 完全一致：
    - with_structured_output(method="function_calling") 做 schema 强制执行
    - function calling 失败 → json_mode 回退（_fallback_invoke）
    - LangChain 内置 max_retries 处理网络级重试

    Args:
        llm:         LangChain ChatModel 实例。不传则自动创建（DashScope Qwen-VL）。
        concurrency: 批量分析时的最大并发数（默认 3）。
    """

    # DashScope Qwen-VL 默认配置
    VL_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    VL_MODEL = "qwen3-vl-plus"

    def __init__(
        self,
        llm: Optional[ChatOpenAI] = None,
        concurrency: int = 3,
    ):
        self.llm = llm or create_chat_model(
            base_url=self.VL_BASE_URL,
            api_key=settings.dashscope_api_key.get_secret_value(),
            model=self.VL_MODEL,
            max_tokens=1000,
        )
        self.concurrency = concurrency

        if self.llm is None:
            # API key 未设置，所有方法返回 None
            self._structured_llm = None
            self._chain = None
            self._semaphore = asyncio.Semaphore(concurrency)
            return

        # ---- 构建 LangChain chain ----
        # 与 M3 ReviewAnalyzer 相同的模式：
        # with_structured_output() 使用 function calling 强制 JSON Schema 匹配。
        self._structured_llm = self.llm.with_structured_output(
            ImageDescription, method="function_calling"
        )

        # chain: 准备多模态消息 → structured LLM
        self._chain = (
            RunnableLambda(self._build_message, name="prepare_vl_message")
            | self._structured_llm
        )

        # 并发控制信号量
        self._semaphore = asyncio.Semaphore(concurrency)

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    async def describe(self, image_path_or_url: str) -> Optional[ImageDescription]:
        """分析单张图片。

        内置 function_calling → json_mode 回退（与 M3 一致）。
        LangChain 处理网络级重试。

        Args:
            image_path_or_url: 图片本地路径或远程 URL。

        Returns:
            ImageDescription，失败返回 None。
        """
        if self.llm is None:
            logger.error("dashscope_api_key 未设置，无法调用 Qwen-VL")
            return None

        # 准备图片数据
        image_data = self._prepare_image(image_path_or_url)
        if image_data is None:
            return None

        try:
            result: ImageDescription = await self._chain.ainvoke({
                "image_data": image_data,
            })
            return result
        except OutputParserException:
            # function_calling 失败时回退 json_mode（参照 M3）
            logger.warning(
                f"function_calling failed for {image_path_or_url!r}, "
                f"falling back to json_mode."
            )
            try:
                return await self._fallback_invoke(image_data)
            except Exception as e:
                logger.error(
                    f"json_mode fallback also failed for {image_path_or_url!r}: {e}"
                )
                return None
        except Exception as e:
            logger.error(f"VL 调用失败 [{image_path_or_url!r}]: {e}")
            return None

    async def describe_batch(
        self,
        images: List[str],
        concurrency: Optional[int] = None,
    ) -> List[Optional[ImageDescription]]:
        """批量分析图片（异步并发）。

        与 M3 ReviewAnalyzer.analyze_batch() 模式一致。

        Args:
            images:      图片路径列表。
            concurrency: 覆盖默认并发数。

        Returns:
            与输入顺序对应的 ImageDescription 列表，失败项为 None。
        """
        sem = (
            asyncio.Semaphore(concurrency)
            if concurrency is not None
            else self._semaphore
        )

        async def _one(image_path: str) -> Optional[ImageDescription]:
            async with sem:
                return await self.describe(image_path)

        tasks = [_one(img) for img in images]
        c = concurrency or self.concurrency
        logger.info(
            f"开始批量多模态描述: {len(images)} 张图片, concurrency={c}"
        )

        raw = await asyncio.gather(*tasks, return_exceptions=True)

        results: List[Optional[ImageDescription]] = []
        for i, item in enumerate(raw):
            if isinstance(item, Exception):
                logger.error(f"图片 {images[i]!r}: 未处理的异常: {item}")
                results.append(None)
            else:
                results.append(item)

        success = sum(1 for r in results if r is not None)
        logger.info(f"批量描述完成: {success}/{len(images)} 成功")
        return results

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _build_message(inputs: dict) -> list:
        """构建多模态 HumanMessage。

        chain 的第一环：将 {"image_data": {...}} 转为 LangChain 消息格式。

        Args:
            inputs: {"image_data": {"type": "image_url", "image_url": {...}}}

        Returns:
            [HumanMessage(content=[image_block, text_block])]
        """
        image_data = inputs["image_data"]
        return [
            HumanMessage(content=[
                image_data,
                {"type": "text", "text": DESCRIPTION_PROMPT},
            ])
        ]

    async def _fallback_invoke(self, image_data: dict) -> ImageDescription:
        """json_mode 回退方案（参照 M3 ReviewAnalyzer._fallback_invoke）。

        当 function_calling 不可用时，用 json_mode 获取原始 JSON 字符串，
        然后 Pydantic 校验。最多重试 3 次。

        Args:
            image_data: 图片数据字典。

        Returns:
            ImageDescription 实例。

        Raises:
            最后一次重试的异常（3 次全部失败时）。
        """
        llm_json = self.llm.with_structured_output(
            ImageDescription, method="json_mode"
        )
        chain_json = RunnableLambda(self._build_message) | llm_json

        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                result = await chain_json.ainvoke({
                    "image_data": image_data,
                })
                return result
            except Exception as e:
                last_error = e
                logger.warning(
                    f"json_mode attempt {attempt}/3 failed: {e}"
                )
                if attempt < 3:
                    await asyncio.sleep(1.0 * attempt)

        raise last_error  # type: ignore[misc]

    @staticmethod
    def _prepare_image(image_path_or_url: str) -> Optional[dict]:
        """准备图片数据。

        本地路径 → base64 data URL。
        远程 URL → 直接使用 URL。

        Returns:
            OpenAI 兼容的 image_url 内容块，或 None。
        """
        # 远程 URL 直接使用
        if image_path_or_url.startswith(("http://", "https://")):
            return {"type": "image_url", "image_url": {"url": image_path_or_url}}

        # 本地路径 → base64
        path = Path(image_path_or_url)
        if not path.exists():
            logger.error(f"图片文件不存在: {image_path_or_url}")
            return None

        try:
            suffix = path.suffix.lower()
            mime_map = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".gif": "image/gif",
            }
            mime_type = mime_map.get(suffix, "image/jpeg")

            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")

            data_url = f"data:{mime_type};base64,{b64}"
            return {"type": "image_url", "image_url": {"url": data_url}}
        except Exception as e:
            logger.error(f"图片编码失败 [{image_path_or_url}]: {e}")
            return None
