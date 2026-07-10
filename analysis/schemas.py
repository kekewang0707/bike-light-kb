"""Pydantic schemas for LLM structured output.

这些模型定义 LLM 必须产出的精确 JSON 结构。
用于 LangChain 的 with_structured_output() 做 schema 强制执行，
同时在写入数据库前提供运行时校验。

每个子模型对应 ReviewAnalysis 表的一个 JSONB 列。
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


# ============================================================================
# 卖点 (SellingPoint) — 对应 ReviewAnalysis.selling_points
# ============================================================================

class SellingPoint(BaseModel):
    """用户好评的产品特性（卖点）。"""

    aspect: str = Field(
        description="具体特性名称，如'亮度高'、'续航久'、'安装方便'、'做工精致'",
    )
    mention: str = Field(
        description="用户原话引用，≤10字，必须直接引用评价原文，不可改写",
    )
    sentiment: Literal["positive", "neutral"] = Field(
        description="情感倾向：positive=好评推荐, neutral=中性提及",
    )


# ============================================================================
# 使用场景 (UseScene) — 对应 ReviewAnalysis.use_scenes
# ============================================================================

class UseScene(BaseModel):
    """用户实际使用场景。"""

    scene: str = Field(
        description="场景描述，如'夜间山路骑行'、'城市通勤'、'雨天骑行'、'周末郊游'",
    )
    detail: str = Field(
        description="用户原话引用，体现该使用场景的关键表述",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="推断置信度，0.0-1.0。用户明确描述场景时接近1.0，仅暗示时接近0.3",
    )


# ============================================================================
# 痛点 (PainPoint) — 对应 ReviewAnalysis.pain_points
# ============================================================================

class PainPoint(BaseModel):
    """用户不满或吐槽的方面（痛点）。"""

    aspect: str = Field(
        description="问题点，如'续航不够'、'支架松动'、'进水'、'充电口设计差'",
    )
    mention: str = Field(
        description="用户原话引用，≤10字，必须直接引用评价原文，不可改写",
    )
    severity: Literal["high", "medium", "low"] = Field(
        description="严重程度：high=严重影响使用/安全, medium=影响体验, low=轻微不满",
    )


# ============================================================================
# 用户画像 (UserProfile) — 对应 ReviewAnalysis.user_profile
# ============================================================================

class UserProfile(BaseModel):
    """推断的用户画像。无法推断的字段填'不确定'。"""

    rider_type: Literal["通勤", "运动", "长途", "外卖", "不确定"] = Field(
        description="骑行者类型",
    )
    bike_type: Literal["山地车", "公路车", "折叠车", "电助力", "不确定"] = Field(
        description="用户使用的车型",
    )
    concern_priority: list[str] = Field(
        default_factory=list,
        description=(
            "用户关注优先级列表，按重要性排序。"
            "可选值：'价格敏感'、'性能优先'、'品牌倾向'、'性价比'、'外观设计'、'安全第一'"
        ),
    )


# ============================================================================
# 完整提取结果 (ReviewExtraction) — 一次 LLM 调用产出
# ============================================================================

class ReviewExtraction(BaseModel):
    """单条评价的完整结构化提取结果。

    此模型直接映射到 ReviewAnalysis 表的四个 JSONB 列 + summary 字段。
    一次 LLM 调用同时提取所有维度，节省 API 成本。
    """

    selling_points: list[SellingPoint] = Field(
        default_factory=list,
        description="用户好评的产品特性列表。如果评价中没有明确好评，返回空列表。",
    )
    use_scenes: list[UseScene] = Field(
        default_factory=list,
        description="用户实际使用场景列表。如果评价未提及使用场景，返回空列表。",
    )
    pain_points: list[PainPoint] = Field(
        default_factory=list,
        description="用户不满的痛点列表。如果评价中没有负面内容，返回空列表。",
    )
    user_profile: UserProfile | None = Field(
        default=None,
        description="推断的用户画像。无法推断时返回 null（非'不确定'字符串的字段）。",
    )
    summary: str = Field(
        default="",
        description="一句话概括评价核心观点，中文，≤50字。如'用户对亮度和续航满意，主要用于夜间通勤，注重性价比。'",
    )
