"""爬虫数据结构定义 — 所有爬虫模块共享的数据类型。

采用 dataclass 而非 dict：
- 类型安全：IDE 自动补全、mypy 静态检查
- 自文档化：字段名即文档
- 易于序列化：dataclasses.asdict() 直接转 dict
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from decimal import Decimal
from typing import List, Optional


# ============================================================================
# 搜索列表 → 商品简要信息
# ============================================================================

@dataclass
class ProductBrief:
    """搜索列表返回的商品简要信息。

    从搜索页提取，包含基本信息但不含规格参数和详情图。
    这一步速度快（API ~200ms），是后续详情抓取的前置步骤。
    """

    platform: str               # jd / taobao / pdd
    platform_id: str            # 平台商品ID（京东为 skuId）
    name: str                   # 商品标题（原始文案）
    price: Decimal              # 当前售价（元）
    shop_name: str              # 店铺名称
    main_image_url: str         # 商品主图 URL
    sales_volume: int           # 累计销量（平台展示值，非精确值）
    comment_count: int          # 评价总数
    good_rate: Optional[Decimal] = None   # 好评率(%)，如 97.00
    product_url: str = ""       # 商品详情页链接


# ============================================================================
# 详情页 → 完整商品信息
# ============================================================================

@dataclass
class SkuData:
    """单个 SKU 规格数据。"""

    sku_name: str               # SKU 名称，如 "黑色-800流明-续航8小时"
    price: Decimal              # 该 SKU 的售价
    stock: int = 0              # 库存（平台展示值）
    sku_specs: dict = field(default_factory=dict)  # {"颜色":"黑色","流明":800}


@dataclass
class ProductDetail:
    """商品详情页抓取结果。

    从详情页提取，包含规格参数、SKU、详情图。
    使用 Playwright 渲染后解析（详情页规格区域通常需要 JS 渲染）。
    """

    platform_id: str
    brand: Optional[str] = None
    category: Optional[str] = None
    specs: dict = field(default_factory=dict)   # {"流明":800,"防水":"IPX6",...}
    skus: List[SkuData] = field(default_factory=list)
    detail_images: List[str] = field(default_factory=list)  # 详情图 URL 列表
    original_price: Optional[Decimal] = None


# ============================================================================
# 评价 → 用户评价数据
# ============================================================================

@dataclass
class ReviewData:
    """评价数据 — 从评价列表页或 XHR 响应中提取。"""

    platform_review_id: str     # 平台侧评价唯一ID
    rating: int                 # 星级 1-5
    content: str                # 评价原文
    user_name: str              # 用户昵称（平台已脱敏）
    user_level: Optional[str] = None    # 用户等级: plus会员 / 普通用户
    review_date: Optional[datetime] = None
    buy_date: Optional[datetime] = None
    images: List[str] = field(default_factory=list)    # 晒图 URL 列表
    likes: int = 0              # 被点赞数
    reply_content: Optional[str] = None     # 卖家回复


# ============================================================================
# 图片 → 下载任务
# ============================================================================

@dataclass
class ImageTask:
    """图片下载任务 — 由 Pipeline 生成，交给 ImageDownloader 执行。"""

    url: str                    # 远程图片 URL
    product_id: int             # 数据库中的商品 ID（Product.id）
    image_type: str             # main / detail / review
    local_path: str             # 本地存储路径，如 data/images/1/main_1.jpg
    sort_order: int = 0         # 排序序号


# ============================================================================
# 爬虫执行报告
# ============================================================================

@dataclass
class CrawlReport:
    """爬虫执行报告 — 每次 run() 返回一份报告。"""

    keyword: str                            # 搜索关键词
    products_found: int = 0                 # 搜索到的商品数
    products_new: int = 0                   # 新入库商品数
    products_updated: int = 0               # 更新商品数
    reviews_collected: int = 0              # 采集的评价数
    images_downloaded: int = 0              # 成功下载的图片数
    images_failed: int = 0                  # 下载失败的图片数
    errors: List[str] = field(default_factory=list)    # 错误信息列表
    captcha_triggered: bool = False         # 是否触发了验证码
    duration_seconds: float = 0.0           # 总耗时（秒）
    started_at: Optional[datetime] = None   # 开始时间
    finished_at: Optional[datetime] = None  # 结束时间

    def to_dict(self) -> dict:
        """转为字典，方便序列化和日志记录。"""
        return asdict(self)

    @property
    def success_rate(self) -> float:
        """图片下载成功率。"""
        total = self.images_downloaded + self.images_failed
        return self.images_downloaded / total if total > 0 else 1.0


# ============================================================================
# 价格历史
# ============================================================================

@dataclass
class PricePoint:
    """单个价格数据点 — 用于 get_price_history() 的返回值。"""

    price: Decimal
    date: datetime
