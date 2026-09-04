"""商品相关 ORM 模型：Product、ProductImage、ProductSKU。

本模块定义电商知识库最核心的三个模型：

Product (商品主表)
    └── 存储商品的完整信息，包括平台ID、价格、销量、规格参数等。
        自行车灯的规格字段因品牌而异（有的标流明、有的标照射距离），
        因此 specs 使用 JSONB 类型灵活存储。

ProductImage (商品图片)
    └── 与 Product 多对一关系。
        区分主图(main)、详情图(detail)、视频封面(video_cover)三种类型。
        同时记录远程 URL 和下载后的本地路径。

ProductSKU (SKU 规格)
    └── 与 Product 多对一关系。
        一个商品可能有多个 SKU（如"黑色-800流明"和"银色-1000流明"），
        每个 SKU 独立定价和库存。

关系链::

    Product 1──N ProductImage
    Product 1──N ProductSKU
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    BigInteger,
    Integer,
    String,
    Numeric,
    DateTime,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from models.base import Base


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _utcnow():
    """返回当前 UTC 时间，带时区信息。

    所有时间字段统一使用 UTC 存储：
    - 避免多时区数据混乱
    - 展示层根据用户时区自行转换
    - 数据库查询/排序不会受夏令时影响
    """
    return datetime.now(timezone.utc)


# ============================================================================
# Product — 商品主表
# ============================================================================

class Product(Base):
    """商品主表 — 存储从电商平台采集的自行车灯商品完整信息。

    关键设计:
    - platform + platform_id 组成唯一约束，同一平台同一商品多次采集自动去重
    - specs 使用 JSONB：自行车灯规格字段因品牌而异，JSONB 灵活适配
    - price 使用 DECIMAL：精确十进制，避免浮点数精度问题
    - 所有时间字段带时区，统一 UTC 存储
    """

    __tablename__ = "products"

    # ---- 主键 ----
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # ---- 平台标识 ----
    platform = Column(
        String(20), nullable=False, default="taobao",
        comment="电商平台: taobao(淘宝) / pdd(拼多多)",
    )
    platform_id = Column(
        String(100), nullable=False,
        comment="平台侧的商品唯一ID，如淘宝的 itemId",
    )

    # ---- 基础信息 ----
    name = Column(String(500), nullable=False, comment="商品标题（原始文案）")
    brand = Column(String(200), comment="品牌名称")
    shop_name = Column(String(300), comment="店铺名称")
    category = Column(String(200), comment="品类，如'自行车前灯'、'自行车尾灯'")
    product_url = Column(String(1000), comment="商品详情页链接")
    main_image_url = Column(String(1000), comment="商品主图远程URL")

    # ---- 价格 ----
    price = Column(Numeric(10, 2), comment="当前售价（元）")
    original_price = Column(Numeric(10, 2), comment="原价/划线价（元），用于计算折扣")

    # ---- 运营数据 ----
    sales_volume = Column(Integer, default=0, comment="累计销量（平台展示值）")
    comment_count = Column(Integer, default=0, comment="评价总数")
    good_rate = Column(Numeric(5, 2), comment="好评率(%)，如 97.00 表示 97%")

    # ---- 规格参数 ----
    specs = Column(
        JSONB,
        comment=(
            '规格参数，JSON 对象。示例: '
            '{"流明":800, "防水等级":"IPX6", "电池容量":"2000mAh", '
            '"充电方式":"USB-C", "安装方式":"车把快拆", "重量":"150g"}'
        ),
    )

    # ---- 营销/榜单附加信息（从 specs 剥离，避免污染规格参数）----
    marketing = Column(
        JSONB,
        comment=(
            "商品卡附加信息：营销USP/榜单/热度/销量文案/评价摘录等，"
            "来自搜索 itemsArray，与规格参数(specs)分开存储"
        ),
    )

    # ---- 时间戳 ----
    crawled_at = Column(
        DateTime(timezone=True),
        comment="最后一次爬虫采集此商品数据的时间",
    )
    created_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
        comment="记录首次创建时间",
    )
    updated_at = Column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False,
        comment="记录最后更新时间（每次 UPDATE 自动刷新）",
    )

    # ==================================================================
    # 关系映射
    # ==================================================================
    # cascade="all, delete-orphan": 删除 Product 时级联删除所有关联子记录
    # lazy="select":           默认加载策略 — 访问属性时才发 SQL 查询

    images = relationship(
        "ProductImage", back_populates="product",
        cascade="all, delete-orphan",
        doc="商品图片列表（一对多）",
    )
    skus = relationship(
        "ProductSKU", back_populates="product",
        cascade="all, delete-orphan",
        doc="SKU 规格列表（一对多）",
    )
    reviews = relationship(
        "Review", back_populates="product",
        cascade="all, delete-orphan",
        doc="评价列表（一对多）",
    )
    price_history = relationship(
        "PriceHistory", back_populates="product",
        cascade="all, delete-orphan",
        doc="价格历史记录（一对多）",
    )
    rankings = relationship(
        "HotRanking", back_populates="product",
        cascade="all, delete-orphan",
        doc="热榜上榜记录（一对多）",
    )

    # ==================================================================
    # 表级约束
    # ==================================================================
    __table_args__ = (
        UniqueConstraint(
            "platform", "platform_id",
            name="uq_product_platform_id",
        ),
        {"comment": "商品主表"},
    )

    def __repr__(self):
        return f"<Product(id={self.id}, platform={self.platform}, name={self.name!r})>"


# ============================================================================
# ProductImage — 商品图片
# ============================================================================

class ProductImage(Base):
    """商品图片 — 与 Product 多对一，与 ImageAnalysis 一对一。

    图片分为三种类型：
    - main:         商品主图（通常 1 张，展示在搜索结果中）
    - detail:       详情页图片（多张，展示商品细节、卖点、使用场景）
    - video_cover:  视频封面图

    每张图片同时保存原始 URL（用于追溯）和本地路径（用于快速访问）。
    """

    __tablename__ = "product_images"

    # ---- 主键 ----
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # ---- 外键 ----
    product_id = Column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False,
        comment="所属商品ID",
    )

    # ---- 图片信息 ----
    image_type = Column(
        String(20), nullable=False, default="main",
        comment="图片类型: main(主图) / detail(详情图) / video_cover(视频封面)",
    )
    image_url = Column(String(1000), comment="远程图片 URL")
    local_path = Column(String(500), comment="下载后的本地存储路径")
    sort_order = Column(
        Integer, default=0,
        comment="同一商品内图片的排序序号，值越小越靠前",
    )

    # ==================================================================
    # 关系映射
    # ==================================================================
    product = relationship(
        "Product", back_populates="images",
        doc="所属商品（多对一）",
    )
    analysis = relationship(
        "ImageAnalysis", back_populates="image",
        uselist=False,              # 一对一关系，返回单个对象而非列表
        cascade="all, delete-orphan",
        doc="图片分析结果（一对一）",
    )

    __table_args__ = ({"comment": "商品图片表"},)

    def __repr__(self):
        return f"<ProductImage(id={self.id}, type={self.image_type}, product_id={self.product_id})>"


# ============================================================================
# ProductSKU — 商品 SKU 规格
# ============================================================================

class ProductSKU(Base):
    """商品 SKU 规格 — 与 Product 多对一。

    一个商品可能有多个 SKU，例如：
    - "标配版-800流明"  ¥89, 库存200
    - "高配版-1000流明" ¥119, 库存150

    SKU 的规格属性因商品而异，因此 sku_specs 使用 JSONB 灵活存储。
    """

    __tablename__ = "product_skus"

    # ---- 主键 ----
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # ---- 外键 ----
    product_id = Column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False,
        comment="所属商品ID",
    )

    # ---- SKU 信息 ----
    sku_name = Column(
        String(300),
        comment='SKU 名称，如 "黑色-800流明-续航8小时"',
    )
    price = Column(Numeric(10, 2), comment="该 SKU 的售价（元）")
    stock = Column(Integer, comment="库存数量（平台展示值，非实时）")
    sku_specs = Column(
        JSONB,
        comment=(
            'SKU 规格属性，JSON 对象。'
            '示例: {"颜色":"黑色", "流明":800, "电池":"2000mAh"}'
        ),
    )

    # ==================================================================
    # 关系映射
    # ==================================================================
    product = relationship(
        "Product", back_populates="skus",
        doc="所属商品（多对一）",
    )

    __table_args__ = ({"comment": "商品SKU规格表"},)

    def __repr__(self):
        return f"<ProductSKU(id={self.id}, name={self.sku_name!r}, product_id={self.product_id})>"
