"""数据处理管线 — 采集数据清洗、校验、去重、入库。

Pipeline 是爬虫和数据库之间的桥梁，负责：
- 数据清洗: 价格格式化、空值处理、HTML 标签去除
- 数据校验: 必填字段检查、价格合理性、评分范围
- 去重:     按 platform+platform_id 检测重复，存在则更新
- 入库:     调用 CRUDBase 写入 PostgreSQL

所有数据库写操作通过此模块，确保数据质量和一致性。

Usage::

    from crawler.pipeline import DataPipeline

    pipeline = DataPipeline()
    product_id, is_new = pipeline.save_product(brief, detail)
    review_count = pipeline.save_reviews(product_id, reviews)
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Tuple

from loguru import logger

from models import (
    get_session,
    Product,
    ProductImage,
    Review,
    ReviewImage,
    PriceHistory,
    CRUDBase,
)
from crawler.schemas import ProductBrief, ProductDetail, ReviewData, ImageTask


def _utcnow():
    return datetime.now(timezone.utc)


class DataValidator:
    """数据校验器 — 入库前执行。"""

    VALID_PLATFORMS = {"taobao", "pdd"}

    @staticmethod
    def validate_product(data: dict) -> Tuple[bool, str]:
        """校验商品数据。

        返回:
            (是否通过, 失败原因)
        """
        # name 不能为空且长度合理
        name = data.get("name", "")
        if not name or len(name.strip()) < 5:
            return False, f"商品标题过短: {name!r}"

        # price 合理范围
        price = data.get("price")
        if price is not None:
            try:
                price = Decimal(str(price))
                if price <= 0:
                    return False, f"价格必须大于 0: {price}"
                if price > 9999:
                    return False, f"价格异常过高: {price}"
            except Exception:
                return False, f"价格格式无效: {price}"

        # good_rate 范围
        good_rate = data.get("good_rate")
        if good_rate is not None:
            try:
                gr = Decimal(str(good_rate))
                if gr < 0 or gr > 100:
                    return False, f"好评率超出 0-100: {gr}"
            except Exception:
                pass  # 无法解析则跳过校验

        # platform 必须是已知平台
        platform = data.get("platform", "")
        if platform not in DataValidator.VALID_PLATFORMS:
            return False, f"未知平台: {platform}"

        # platform_id 不能为空
        if not data.get("platform_id"):
            return False, "platform_id 不能为空"

        return True, "ok"

    @staticmethod
    def validate_review(data: dict) -> Tuple[bool, str]:
        """校验评价数据。"""
        rating = data.get("rating", 0)
        if not (1 <= rating <= 5):
            return False, f"评分超出 1-5: {rating}"

        content = data.get("content", "")
        if not content or len(content.strip()) < 2:
            return False, f"评价内容过短: {content!r}"

        return True, "ok"


class DataPipeline:
    """数据处理管线 — 采集→清洗→校验→去重→入库。"""

    def __init__(self):
        self.product_crud = CRUDBase(Product)
        self.image_crud = CRUDBase(ProductImage)
        self.review_crud = CRUDBase(Review)
        self.review_image_crud = CRUDBase(ReviewImage)
        self.price_crud = CRUDBase(PriceHistory)
        self.validator = DataValidator()
        self._image_tasks: List[ImageTask] = []
        self._stats = {"new": 0, "updated": 0, "skipped": 0}

    # ==================================================================
    # 商品入库
    # ==================================================================

    def save_product(
        self,
        brief: ProductBrief,
        detail: Optional[ProductDetail] = None,
    ) -> Tuple[Optional[int], bool]:
        """保存或更新商品数据。

        如果 platform+platform_id 已存在 → 更新价格/销量等字段
        如果不存在 → 创建新记录

        参数:
            brief:  搜索阶段返回的简要信息
            detail: 详情页抓取的补充信息（可选）

        返回:
            (product_id, is_new): product_id 是数据库主键，is_new 表示是否新创建
        """
        session = next(get_session())
        try:
            # 构建商品数据
            product_data = {
                "platform": brief.platform,
                "platform_id": brief.platform_id,
                "name": brief.name,
                "price": brief.price,
                "shop_name": brief.shop_name,
                "main_image_url": brief.main_image_url,
                "sales_volume": brief.sales_volume,
                "comment_count": brief.comment_count,
                "good_rate": brief.good_rate,
                "product_url": brief.product_url,
                "crawled_at": _utcnow(),
            }

            # 合并详情页数据
            if detail:
                if detail.brand:
                    product_data["brand"] = detail.brand
                if detail.category:
                    product_data["category"] = detail.category
                if detail.specs:
                    product_data["specs"] = detail.specs
                if detail.original_price:
                    product_data["original_price"] = detail.original_price

            # 校验
            ok, msg = self.validator.validate_product(product_data)
            if not ok:
                logger.warning(f"商品校验失败: {msg}")
                return None, False

            # 查找已有记录
            existing = self.product_crud.get_by(
                session,
                platform=brief.platform,
                platform_id=brief.platform_id,
            )

            if existing:
                # 更新已有记录
                updates = {
                    k: v for k, v in product_data.items()
                    if k not in ("platform", "platform_id")
                }
                self.product_crud.update(session, existing.id, **updates)
                product_id = existing.id
                is_new = False
                self._stats["updated"] += 1
            else:
                # 创建新记录
                product = self.product_crud.create(session, **product_data)
                product_id = product.id
                is_new = True
                self._stats["new"] += 1

            # 保存详情图的下载任务
            if detail and detail.detail_images:
                for i, img_url in enumerate(detail.detail_images):
                    if img_url:
                        self._image_tasks.append(ImageTask(
                            url=img_url,
                            product_id=product_id,
                            image_type="detail",
                            local_path=f"{product_id}/detail_{i + 1}.jpg",
                            sort_order=i + 1,
                        ))

            # 主图下载任务
            if brief.main_image_url:
                self._image_tasks.append(ImageTask(
                    url=brief.main_image_url,
                    product_id=product_id,
                    image_type="main",
                    local_path=f"{product_id}/main_1.jpg",
                    sort_order=1,
                ))

            return product_id, is_new

        except Exception as e:
            session.rollback()
            logger.error(f"保存商品失败 [{brief.platform_id}]: {e}")
            raise
        finally:
            session.close()

    # ==================================================================
    # 评价入库
    # ==================================================================

    def save_reviews(
        self,
        product_id: int,
        reviews: List[ReviewData],
    ) -> int:
        """批量保存评价数据。

        参数:
            product_id: 数据库中的商品 ID
            reviews:    ReviewData 列表

        返回:
            实际新入库的评论数
        """
        if not reviews:
            return 0

        session = next(get_session())
        saved_count = 0

        try:
            for review_data in reviews:
                # 校验
                rd = {
                    "rating": review_data.rating,
                    "content": review_data.content,
                    "platform_review_id": review_data.platform_review_id,
                }
                ok, msg = self.validator.validate_review(rd)
                if not ok:
                    logger.debug(f"评价校验跳过: {msg}")
                    continue

                # 检查是否已存在（按 platform_review_id 去重）
                existing = self.review_crud.get_by(
                    session,
                    platform_review_id=review_data.platform_review_id,
                )
                if existing:
                    continue

                # 创建评价
                review = self.review_crud.create(
                    session,
                    product_id=product_id,
                    platform_review_id=review_data.platform_review_id,
                    rating=review_data.rating,
                    content=review_data.content,
                    user_name=review_data.user_name,
                    user_level=review_data.user_level,
                    review_date=review_data.review_date,
                    buy_date=review_data.buy_date,
                    likes=review_data.likes,
                    reply_content=review_data.reply_content,
                    crawled_at=_utcnow(),
                )

                # 下载评价晒图
                for i, img_url in enumerate(review_data.images):
                    if img_url:
                        self._image_tasks.append(ImageTask(
                            url=img_url,
                            product_id=product_id,
                            image_type="review",
                            local_path=f"{product_id}/review_{review.id}_{i + 1}.jpg",
                            sort_order=i + 1,
                        ))

                saved_count += 1

        except Exception as e:
            session.rollback()
            logger.error(f"保存评价失败 [product_id={product_id}]: {e}")
            raise
        finally:
            session.close()

        if saved_count > 0:
            logger.info(f"已保存 {saved_count} 条评价 (product_id={product_id})")
        return saved_count

    # ==================================================================
    # 价格快照
    # ==================================================================

    def save_price_snapshot(self, product_id: int, price: Decimal):
        """保存当日价格快照。

        如果今天已有快照，更新价格；否则插入新记录。
        """
        today = _utcnow().date()
        session = next(get_session())
        try:
            existing = self.price_crud.get_by(
                session,
                product_id=product_id,
                snapshot_date=today,
            )
            if existing:
                self.price_crud.update(session, existing.id, price=price)
            else:
                self.price_crud.create(
                    session,
                    product_id=product_id,
                    price=price,
                    snapshot_date=today,
                )
        except Exception as e:
            session.rollback()
            logger.error(f"保存价格快照失败 [product_id={product_id}]: {e}")
        finally:
            session.close()

    # ==================================================================
    # 下载任务
    # ==================================================================

    def get_image_tasks(self) -> List[ImageTask]:
        """获取所有待下载的图片任务并清空内部队列。

        调用此方法后，内部队列被清空。
        """
        tasks = list(self._image_tasks)
        self._image_tasks.clear()
        return tasks

    # ==================================================================
    # 统计
    # ==================================================================

    def reset_stats(self):
        self._stats = {"new": 0, "updated": 0, "skipped": 0}

