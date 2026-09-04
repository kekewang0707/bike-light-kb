"""评价相关 ORM 模型：Review、ReviewImage。

本模块定义电商评价体系的 ORM 模型：

Review (评价主表)
    └── 存储单条评价的完整信息，包括评分(1-5星)、评价原文、用户信息。
        与 Product 多对一、与 ReviewAnalysis 一对一。
        评价是选品分析和内容创作最重要的数据来源。

ReviewImage (评价晒图)
    └── 与 Review 多对一关系。
        存储用户在评价中上传的实拍图片（晒图），
        比商品主图更能反映真实使用场景。

关系链::

    Product 1──N Review 1──N ReviewImage
                     Review 1──1 ReviewAnalysis
"""

from sqlalchemy import (
    Column,
    BigInteger,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
)
from sqlalchemy.orm import relationship

from models.base import Base


class Review(Base):
    """评价主表 — 存储从电商平台采集的用户评价。

    关键设计:
    - rating 存原始 1-5 星评分，不做归一化，方便按平台原始口径统计
    - content 使用 TEXT 类型，支持长文本评价
    - user_level 区分用户等级（plus会员 vs 普通用户），高等级用户评价参考价值更高
    - reply_content 存卖家回复，可用于分析品牌的售后策略和话术
    """

    __tablename__ = "reviews"

    # ---- 主键 ----
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # ---- 外键 ----
    product_id = Column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False,
        comment="被评价的商品ID",
    )

    # ---- 平台标识 ----
    platform_review_id = Column(
        String(100),
        comment="平台侧的评价唯一ID，用于去重和追溯",
    )

    # ---- 评价核心 ----
    rating = Column(
        Integer, nullable=False,
        comment="星级评分 (1-5)，5 为最高",
    )
    content = Column(
        Text,
        comment="评价原文，保留用户的完整表述",
    )

    # ---- 用户信息 ----
    user_name = Column(
        String(200),
        comment="用户昵称（已脱敏/留空；明文在采集时即丢弃，仅保留哈希用于去重）",
    )
    user_name_hash = Column(
        String(64),
        comment="用户昵称的盐值SHA-256哈希（明文已丢弃，仅用于同用户去重）",
    )
    user_level = Column(
        String(50),
        comment="用户等级，如 plus会员 / 普通用户 / PLUS会员",
    )

    # ---- 时间轴 ----
    buy_date = Column(
        DateTime(timezone=True),
        comment="用户购买日期（平台展示的粗略时间）",
    )
    review_date = Column(
        DateTime(timezone=True),
        comment="用户发表评价的日期",
    )

    # ---- 互动数据 ----
    likes = Column(Integer, default=0, comment="其他用户对这条评价的点赞数")
    reply_content = Column(
        Text,
        comment="卖家对这条评价的回复内容（可分析品牌话术）",
    )

    # ---- 采集元数据 ----
    crawled_at = Column(
        DateTime(timezone=True),
        comment="爬虫采集这条评价的时间",
    )

    # ==================================================================
    # 关系映射
    # ==================================================================
    product = relationship(
        "Product", back_populates="reviews",
        doc="所属商品（多对一）",
    )
    images = relationship(
        "ReviewImage", back_populates="review",
        cascade="all, delete-orphan",
        doc="评价晒图列表（一对多）",
    )
    analysis = relationship(
        "ReviewAnalysis", back_populates="review",
        uselist=False,              # 一对一：每条评价对应一份分析结果
        cascade="all, delete-orphan",
        doc="评价语义分析结果（一对一）",
    )

    __table_args__ = ({"comment": "评价主表"},)

    def __repr__(self):
        return f"<Review(id={self.id}, rating={self.rating}, product_id={self.product_id})>"


class ReviewImage(Base):
    """评价晒图 — 与 Review 多对一。

    用户在评价中上传的实拍图片：
    - 比商品详情图更真实地反映产品实际外观和使用场景
    - 可用于验证商品描述的准确性
    - 可作为内容创作的素材参考
    """

    __tablename__ = "review_images"

    # ---- 主键 ----
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # ---- 外键 ----
    review_id = Column(
        BigInteger, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False,
        comment="所属评价ID",
    )

    # ---- 图片信息 ----
    image_url = Column(String(1000), comment="远程图片 URL")
    local_path = Column(String(500), comment="下载后的本地存储路径")

    # ==================================================================
    # 关系映射
    # ==================================================================
    review = relationship(
        "Review", back_populates="images",
        doc="所属评价（多对一）",
    )

    __table_args__ = ({"comment": "评价晒图表"},)

    def __repr__(self):
        return f"<ReviewImage(id={self.id}, review_id={self.review_id})>"
