"""M3 — 评价分析引擎 (Review Analysis Engine)。

使用 LangChain + DeepSeek API 从评价原文中提取结构化知识：

- selling_points: 卖点（用户好评的产品特性）
- use_scenes:     使用场景（用户实际使用情境）
- pain_points:    痛点（用户吐槽或不满）
- user_profile:   用户画像（骑行者类型、车型、关注优先级）
- summary:        评价摘要

分析结果写入 review_analysis 表（JSONB 字段），
后续由 M5 知识库服务层同步到 ChromaDB 向量存储。

主要入口类::

    from analysis import create_chat_model, ReviewAnalyzer, BatchRunner

    # 单条分析
    llm = create_chat_model()
    analyzer = ReviewAnalyzer(llm, session)
    result = await analyzer.analyze_single(review)

    # 批量分析
    runner = BatchRunner(analyzer, concurrency=5)
    stats = await runner.run(limit=100)
"""

from analysis.schemas import (
    SellingPoint,
    UseScene,
    PainPoint,
    UserProfile,
    ReviewExtraction,
)
from analysis.llm_client import create_chat_model
from analysis.review_analyzer import ReviewAnalyzer
from analysis.batch_runner import BatchRunner, BatchResult

__all__ = [
    # ---- Schemas ----
    "SellingPoint",
    "UseScene",
    "PainPoint",
    "UserProfile",
    "ReviewExtraction",
    # ---- LLM ----
    "create_chat_model",
    # ---- Analyzer ----
    "ReviewAnalyzer",
    # ---- Runner ----
    "BatchRunner",
    "BatchResult",
]
