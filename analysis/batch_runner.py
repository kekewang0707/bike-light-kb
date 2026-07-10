"""BatchRunner — 批量分析调度器。

高阶编排器，负责完整生命周期：
1. 从数据库查询待分析的评价
2. 批量去重（一次 SQL 查已分析的 ID）
3. 按子批次分发到 ReviewAnalyzer
4. 保存结果并返回统计

用法::

    from models import get_session
    from analysis import ReviewAnalyzer, BatchRunner

    session = next(get_session())
    try:
        analyzer = ReviewAnalyzer(session=session)
        runner = BatchRunner(analyzer, concurrency=5)
        result = await runner.run(limit=50)
        print(result)  # BatchResult(analyzed=48, failed=2, ...)
    finally:
        session.close()
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger
from sqlalchemy import select

from models import Review, ReviewAnalysis, CRUDBase
from analysis.review_analyzer import ReviewAnalyzer


# ============================================================================
# 结果类型
# ============================================================================

@dataclass
class BatchResult:
    """批量运行统计。

    遵循 crawler/schemas.py 的 @dataclass 模式（如 CrawlReport）。
    """

    total: int = 0
    analyzed: int = 0
    failed: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


# ============================================================================
# 调度器
# ============================================================================

class BatchRunner:
    """批量分析调度器。

    Args:
        analyzer:   ReviewAnalyzer 实例（需持有有效的 session）。
        batch_size: 每子批次处理的评价数（用于进度可见性，默认 50）。
        concurrency: 并发 LLM 请求数上限（默认 5）。
    """

    def __init__(
        self,
        analyzer: ReviewAnalyzer,
        batch_size: int = 50,
        concurrency: int = 5,
    ):
        self.analyzer = analyzer
        self.batch_size = batch_size
        self.concurrency = concurrency
        self._review_crud = CRUDBase(Review)
        self._analysis_crud = CRUDBase(ReviewAnalysis)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        product_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> BatchResult:
        """运行批量分析。

        Args:
            product_id: 限定某商品的评价。不传则处理全部。
            limit:      最大处理条数（用于测试）。不传则不限。

        Returns:
            BatchResult 统计对象。
        """
        t0 = time.time()
        result = BatchResult()
        session = self.analyzer.session

        if session is None:
            raise RuntimeError("ReviewAnalyzer.session is None — must be set before run().")

        # ---- Step 1: 查询评价 ----
        filters = {}
        if product_id is not None:
            filters["product_id"] = product_id

        all_reviews: list[Review] = self._review_crud.list(
            session, limit=limit or 100_000, **filters
        )

        if not all_reviews:
            logger.info("No reviews found.")
            result.duration_seconds = time.time() - t0
            return result

        # ---- Step 2: 批量去重 ----
        review_ids = [r.id for r in all_reviews]

        # 一次查询所有已分析的评价 ID
        analyzed_ids: set[int] = set()
        existing_rows = session.execute(
            select(ReviewAnalysis.review_id).where(
                ReviewAnalysis.review_id.in_(review_ids)
            )
        ).fetchall()
        analyzed_ids = {row[0] for row in existing_rows}

        pending = [r for r in all_reviews if r.id not in analyzed_ids]
        result.total = len(all_reviews)
        result.skipped = len(analyzed_ids)

        if not pending:
            logger.info(
                f"All {len(all_reviews)} reviews already analyzed. "
                f"Skipped: {result.skipped}."
            )
            result.duration_seconds = time.time() - t0
            return result

        logger.info(
            f"BatchRunner: {len(pending)} pending / {result.total} total, "
            f"sub_batch={self.batch_size}, concurrency={self.concurrency}"
        )

        # ---- Step 3: 按子批次分批处理 ----
        total_batches = (len(pending) + self.batch_size - 1) // self.batch_size
        for batch_idx in range(total_batches):
            start = batch_idx * self.batch_size
            end = start + self.batch_size
            batch = pending[start:end]

            logger.info(
                f"Sub-batch {batch_idx + 1}/{total_batches}: "
                f"reviews {start + 1}-{start + len(batch)}"
            )

            analysis_results = await self.analyzer.analyze_batch(
                batch, concurrency=self.concurrency
            )

            # ---- Step 4: 保存结果 ----
            saved = self.analyzer.save_results(analysis_results)
            result.analyzed += saved
            result.failed += len(batch) - saved

        # ---- 最终统计 ----
        result.duration_seconds = round(time.time() - t0, 1)
        logger.info(
            f"BatchRunner complete: {result.analyzed} analyzed, "
            f"{result.failed} failed, {result.skipped} skipped "
            f"[{result.duration_seconds}s]"
        )
        return result
