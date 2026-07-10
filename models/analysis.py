"""分析结果 ORM 模型：ReviewAnalysis、ImageAnalysis。

本模块定义两个分析结果表，对应 M3（评价分析引擎）和 M4（图片分析管道）的输出：

ReviewAnalysis (评价语义分析结果)
    └── LLM 对单条评价的结构化提取，包括：
        - selling_points: 用户好评的产品特性（卖点）
        - use_scenes:     用户实际使用场景
        - pain_points:    用户吐槽或不满的方面（痛点）
        - user_profile:   推断的用户画像

ImageAnalysis (图片分析结果)
    └── 对商品图片的多维度分析，包括：
        - ocr_text:     OCR 提取的文字（如卖点标签、规格标注）
        - description:  多模态模型生成的图片综合描述
        - style_tags:   视觉风格标签
        - clip_vector_id: ChromaDB 中 CLIP 向量的引用

关系链::

    Review         1──1 ReviewAnalysis
    ProductImage   1──1 ImageAnalysis

两个分析结果表同时作为 ChromaDB 向量数据的来源 —
向量化后的卖点/场景/痛点/图片描述会写入 ChromaDB 的 6 个 Collection。
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    BigInteger,
    String,
    Text,
    DateTime,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from models.base import Base


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _utcnow():
    """返回当前 UTC 时间，带时区信息。"""
    return datetime.now(timezone.utc)


# ============================================================================
# ReviewAnalysis — 评价语义分析结果
# ============================================================================

class ReviewAnalysis(Base):
    """评价语义分析结果 — 与 Review 一对一。

    这是 AI 分析管道的核心产出表。LLM 读取评价原文后，
    按预设的 Prompt 模板提取结构化信息写入此表。

    selling_points (JSONB) 数据结构::

        [
            {
                "aspect": "亮度高",           # 特性名称
                "mention": "晚上照得很远",     # 用户原话引用（≤10字）
                "sentiment": "positive"       # positive / neutral
            }
        ]

    use_scenes (JSONB) 数据结构::

        [
            {
                "scene": "夜间山路骑行",       # 场景描述
                "detail": "周末跟车队骑山路",  # 用户原话引用
                "confidence": 0.9             # 推断置信度 0.0-1.0
            }
        ]

    pain_points (JSONB) 数据结构::

        [
            {
                "aspect": "续航不够",          # 问题点
                "mention": "两三天就要充电",    # 用户原话引用
                "severity": "high"             # high / medium / low
            }
        ]

    user_profile (JSONB) 数据结构::

        {
            "rider_type": "通勤",             # 通勤 / 运动 / 长途 / 外卖 / 不确定
            "bike_type": "山地车",            # 山地车 / 公路车 / 折叠车 / 电助力 / 不确定
            "concern_priority": ["性价比", "性能优先"]  # 用户关注优先级
        }
    """

    __tablename__ = "review_analysis"

    # ---- 主键 ----
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # ---- 外键（一对一约束） ----
    review_id = Column(
        BigInteger,
        ForeignKey("reviews.id", ondelete="CASCADE"),
        unique=True,                    # 确保一条评价只有一份分析结果
        nullable=False,
        comment="关联评价ID（与 Review 一对一）",
    )

    # ---- 分析结果 ----
    selling_points = Column(
        JSONB,
        comment='卖点列表 [{"aspect":"亮度高","mention":"晚上照得很远","sentiment":"positive"}]',
    )
    use_scenes = Column(
        JSONB,
        comment='使用场景列表 [{"scene":"夜骑山路","detail":"...","confidence":0.9}]',
    )
    pain_points = Column(
        JSONB,
        comment='痛点列表 [{"aspect":"续航短","mention":"...","severity":"high"}]',
    )
    user_profile = Column(
        JSONB,
        comment='用户画像 {"rider_type":"通勤","bike_type":"山地车","concern_priority":["性价比"]}',
    )
    summary = Column(
        Text,
        comment="LLM 生成的评价摘要（一句话概括评价核心观点）",
    )

    # ---- 分析元数据 ----
    analyzed_at = Column(
        DateTime(timezone=True), default=_utcnow,
        comment="LLM 分析完成的时间",
    )

    # ==================================================================
    # 关系映射
    # ==================================================================
    review = relationship(
        "Review", back_populates="analysis",
        doc="关联的原始评价（多对一/一对一）",
    )

    __table_args__ = ({"comment": "评价语义分析结果表"},)

    def __repr__(self):
        return f"<ReviewAnalysis(id={self.id}, review_id={self.review_id})>"


# ============================================================================
# ImageAnalysis — 图片分析结果
# ============================================================================

class ImageAnalysis(Base):
    """图片分析结果 — 与 ProductImage 一对一。

    这是 M4 图片分析管道的输出表，存储对商品图片的三层分析：

    1. OCR 层 (ocr_text): PaddleOCR 从图片中提取的文字
       → 可搜索图片上的卖点标签、规格标注
       → 示例: "800流明超亮 IPX6防水 USB-C充电"

    2. 语义层 (description): 多模态模型（Qwen-VL/DeepSeek-VL2）生成的综合描述
       → 描述产品外观、安装方式、展示场景、视觉风格
       → 此文本会被向量化后存入 ChromaDB image_descriptions Collection

    3. 向量层 (clip_vector_id): CLIP 模型生成的 512 维图片向量
       → 存储在 ChromaDB image_clip Collection
       → 支持以图搜图和风格聚类分析
    """

    __tablename__ = "image_analysis"

    # ---- 主键 ----
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # ---- 外键（一对一约束） ----
    image_id = Column(
        BigInteger,
        ForeignKey("product_images.id", ondelete="CASCADE"),
        unique=True,                    # 确保一张图片只有一份分析结果
        nullable=False,
        comment="关联图片ID（与 ProductImage 一对一）",
    )

    # ---- OCR 结果 ----
    ocr_text = Column(
        Text,
        comment="PaddleOCR 从图片中提取的完整文字内容",
    )

    # ---- 多模态描述 ----
    description = Column(
        Text,
        comment=(
            "多模态模型(VL)生成的图片综合描述，"
            "包含外观/安装方式/展示场景/视觉风格/文字信息"
        ),
    )

    # ---- 风格标签 ----
    style_tags = Column(
        JSONB,
        comment='视觉风格标签，如 ["极简", "运动风", "科技感", "户外硬核"]',
    )

    # ---- CLIP 向量 ----
    clip_vector_id = Column(
        String(100),
        comment="ChromaDB image_clip Collection 中的向量文档 ID，用于跨系统关联",
    )

    # ---- 分析元数据 ----
    analyzed_at = Column(
        DateTime(timezone=True), default=_utcnow,
        comment="图片分析完成的时间",
    )

    # ==================================================================
    # 关系映射
    # ==================================================================
    image = relationship(
        "ProductImage", back_populates="analysis",
        doc="关联的商品图片（多对一/一对一）",
    )

    __table_args__ = ({"comment": "图片分析结果表"},)

    def __repr__(self):
        return f"<ImageAnalysis(id={self.id}, image_id={self.image_id})>"
