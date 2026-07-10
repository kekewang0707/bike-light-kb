"""ReviewAnalyzer — 评价语义分析器。

核心分析类，编排 LLM 调用（通过 LangChain chain）和数据库写入（通过 CRUDBase）。

职责：
1. 构建 LangChain chain（prompt | structured_llm）
2. analyze_single() — 分析单条评价
3. analyze_batch()  — 异步并发批量分析
4. save_results()   — 分析结果写入 review_analysis 表

用法::

    from models import get_session
    from analysis import create_chat_model, ReviewAnalyzer

    llm = create_chat_model()
    session = next(get_session())
    try:
        analyzer = ReviewAnalyzer(llm, session)
        results = await analyzer.analyze_batch(reviews)
        saved = analyzer.save_results(results)
    finally:
        session.close()
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session
from langchain_openai import ChatOpenAI
from langchain_core.exceptions import OutputParserException

from models import Review, ReviewAnalysis, CRUDBase
from analysis.schemas import ReviewExtraction
from analysis.llm_client import create_chat_model
from analysis.prompts.review_extract import REVIEW_EXTRACTION_PROMPT


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReviewAnalyzer:
    """评价语义分析器。

    核心职责：
    - 构建 LangChain chain（prompt | structured_llm）
    - 单条/批量分析评价
    - 结果持久化

    Args:
        llm:         LangChain ChatModel 实例。不传则用默认配置创建。
        session:     SQLAlchemy 数据库会话。
        concurrency: 批量分析时的最大并发 LLM 请求数（默认 5）。
    """

    def __init__(
        self,
        llm: Optional[ChatOpenAI] = None,
        session: Optional[Session] = None,
        concurrency: int = 5,
    ):
        self.llm = llm or create_chat_model()
        self.session = session
        self.concurrency = concurrency
        self._analysis_crud = CRUDBase(ReviewAnalysis)

        # ---- 构建 LangChain chain ----
        # with_structured_output() 使用 function calling 强制 JSON Schema 匹配。
        # 如果 provider 不支持 function calling，回退到 json_mode（在 analyze_single 中处理）。
        self._structured_llm = self.llm.with_structured_output(
            ReviewExtraction, method="function_calling"
        )
        self._chain = REVIEW_EXTRACTION_PROMPT | self._structured_llm

        # 并发控制信号量
        self._semaphore = asyncio.Semaphore(concurrency)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze_single(
        self, review: Review
    ) -> Optional[ReviewAnalysis]:
        """分析单条评价并返回 ORM 实例（未写入数据库）。

        Args:
            review: Review ORM 实例。

        Returns:
            ReviewAnalysis ORM 实例，失败或内容为空时返回 None。
        """
        if not review.content or not review.content.strip():
            logger.debug(f"Review {review.id}: empty content, skipped.")
            return None

        try:
            extraction: ReviewExtraction = await self._chain.ainvoke({
                "content": review.content,
                "rating": review.rating,
            })
        except OutputParserException:
            # function_calling 失败时回退 json_mode
            logger.warning(
                f"Review {review.id}: function_calling failed, "
                f"falling back to json_mode."
            )
            extraction = await self._fallback_invoke(review)
        except Exception as e:
            logger.error(f"Review {review.id}: LLM invocation failed: {e}")
            return None

        return self._to_orm(review, extraction)

    async def analyze_batch(
        self,
        reviews: list[Review],
        concurrency: Optional[int] = None,
    ) -> list[Optional[ReviewAnalysis]]:
        """异步并发分析多条评价。

        Args:
            reviews:     待分析的 Review 列表。
            concurrency: 覆盖默认并发数。

        Returns:
            与输入顺序对应的 ReviewAnalysis 列表，失败项为 None。
        """
        sem = (
            asyncio.Semaphore(concurrency)
            if concurrency is not None
            else self._semaphore
        )

        async def _one(review: Review) -> Optional[ReviewAnalysis]:
            async with sem:
                return await self.analyze_single(review)

        tasks = [_one(r) for r in reviews]
        n = len(reviews)
        c = concurrency or self.concurrency
        logger.info(
            f"Starting batch analysis: {n} reviews, concurrency={c}"
        )

        raw = await asyncio.gather(*tasks, return_exceptions=True)

        # 分离异常和正常结果
        results: list[Optional[ReviewAnalysis]] = []
        for i, item in enumerate(raw):
            if isinstance(item, Exception):
                logger.error(
                    f"Review {reviews[i].id}: unhandled exception: {item}"
                )
                results.append(None)
            else:
                results.append(item)

        success = sum(1 for r in results if r is not None)
        logger.info(f"Batch complete: {success}/{n} succeeded")
        return results

    def save_results(
        self, results: list[Optional[ReviewAnalysis]]
    ) -> int:
        """将分析结果持久化到数据库。

        通过 CRUDBase.upsert_by() 按 review_id 去重，
        支持对同一评价的重复分析（更新而非报错）。

        Args:
            results: analyze_batch() 返回的结果列表。

        Returns:
            成功保存的记录数。
        """
        if self.session is None:
            raise RuntimeError(
                "No session available. Pass session=... to ReviewAnalyzer."
            )

        saved = 0
        for ra in results:
            if ra is None:
                continue
            try:
                self._analysis_crud.upsert_by(
                    self.session,
                    filters={"review_id": ra.review_id},
                    updates={
                        "selling_points": ra.selling_points,
                        "use_scenes": ra.use_scenes,
                        "pain_points": ra.pain_points,
                        "user_profile": ra.user_profile,
                        "summary": ra.summary,
                        "analyzed_at": _utcnow(),
                    },
                )
                saved += 1
            except Exception as e:
                logger.error(
                    f"Failed to save analysis for review {ra.review_id}: {e}"
                )
        return saved

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fallback_invoke(self, review: Review) -> ReviewExtraction:
        """json_mode 回退方案：让 LLM 返回纯 JSON，再手动解析。

        当 function_calling 不可用时（某些 provider 或旧版 API），
        用 json_mode 获取原始 JSON 字符串，然后 Pydantic 校验。
        """
        llm_json = self.llm.with_structured_output(
            ReviewExtraction, method="json_mode"
        )
        chain_json = REVIEW_EXTRACTION_PROMPT | llm_json

        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                result = await chain_json.ainvoke({
                    "content": review.content,
                    "rating": review.rating,
                })
                return result
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Review {review.id}: json_mode attempt {attempt}/3 failed: {e}"
                )
                if attempt < 3:
                    await asyncio.sleep(1.0 * attempt)

        raise last_error  # type: ignore[misc]

    def _to_orm(
        self, review: Review, extraction: ReviewExtraction
    ) -> ReviewAnalysis:
        """将 LLM 提取结果转为 ReviewAnalysis ORM 实例。

        用 model_dump(mode="json") 将 Pydantic 对象序列化为
        Python dict（JSON 兼容类型），直接存入 JSONB 列。
        """
        return ReviewAnalysis(
            review_id=review.id,
            selling_points=[
                sp.model_dump(mode="json") for sp in extraction.selling_points
            ] or None,
            use_scenes=[
                us.model_dump(mode="json") for us in extraction.use_scenes
            ] or None,
            pain_points=[
                pp.model_dump(mode="json") for pp in extraction.pain_points
            ] or None,
            user_profile=(
                extraction.user_profile.model_dump(mode="json")
                if extraction.user_profile
                else None
            ),
            summary=extraction.summary or None,
            analyzed_at=_utcnow(),
        )
