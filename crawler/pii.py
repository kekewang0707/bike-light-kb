"""个人信息（PII）脱敏工具 — 用户昵称哈希化与保留期匿名化。

合规背景（《个人信息保护法》PIPL）
--------------------------------
评价里的「用户昵称」属于个人信息。本项目只需要"同一用户是否重复出现"这一种能力，
因此遵循最小必要原则：

1. 采集层拿到明文昵称后**立即**计算 pepper 盐值的 SHA-256；
2. **明文不落库**（``reviews.user_name`` 留空），只写 ``user_name_hash``；
3. 超过 ``BKL_DATA_RETENTION_DAYS``（默认 365 天）的记录做**匿名化清理**（哈希置空），
   评价正文与评分保留，不影响分析结论。

为什么用 pepper 而不是裸 SHA-256
--------------------------------
昵称空间很小（"t**8"、"京东用户"），裸哈希可被彩虹表/暴力枚举反推。
pepper 从配置读取（``BKL_PII_PEPPER``），不入库、不进代码库，
拿到数据库 dump 的攻击者在缺失 pepper 的情况下无法还原昵称。

Usage::

    from crawler.pii import hash_user_name, anonymize_expired_reviews

    h = hash_user_name("t**8")            # → 64 位十六进制
    n = anonymize_expired_reviews()       # 清理超期哈希，返回受影响行数
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import hashlib
import hmac

from loguru import logger
from sqlalchemy import func, select, update

from config.settings import settings
from models import Review
from models.base import get_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_user_name(name: Optional[str], pepper: Optional[str] = None) -> Optional[str]:
    """对用户昵称做 pepper 盐值 SHA-256 哈希。

    参数:
        name:   明文昵称（平台返回时通常已做部分掩码）
        pepper: 盐值，默认取 ``settings.pii_pepper``；显式传入便于测试

    返回:
        64 位十六进制字符串；昵称为空时返回 ``None``（不写库）
    """
    if not name:
        return None

    cleaned = name.strip()
    if not cleaned:
        return None

    secret = pepper if pepper is not None else settings.pii_pepper.get_secret_value()
    # hmac 而非裸拼接，避免长度扩展类问题；digest 取 hex 便于入库与索引
    return hmac.new(
        secret.encode("utf-8"),
        cleaned.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def count_expired_reviews(session, cutoff: datetime) -> int:
    """统计仍带哈希、且采集时间早于 cutoff 的评价数量。"""
    stmt = (
        select(func.count())
        .select_from(Review)
        .where(Review.crawled_at < cutoff, Review.user_name_hash.isnot(None))
    )
    return int(session.execute(stmt).scalar_one() or 0)


def anonymize_expired_reviews(
    retention_days: Optional[int] = None,
    session=None,
    dry_run: bool = False,
) -> int:
    """匿名化超期评价中的用户昵称哈希。

    评价正文、评分、晒图等分析价值高的字段全部保留，
    只把 ``user_name_hash``（以及历史遗留的 ``user_name`` 明文）置空。

    参数:
        retention_days: 保留期天数，默认取 ``settings.data_retention_days``
        session:        复用的 SQLAlchemy 会话；不传则内部创建并关闭
        dry_run:        True 时只统计不修改

    返回:
        受影响行数（dry_run 时为"将被清理的行数"）
    """
    days = retention_days if retention_days is not None else settings.data_retention_days
    if days <= 0:
        logger.warning("BKL_DATA_RETENTION_DAYS <= 0，跳过匿名化（0 表示不自动清理）")
        return 0

    cutoff = _utcnow() - timedelta(days=days)
    owns_session = session is None
    if owns_session:
        session = next(get_session())

    try:
        expired = count_expired_reviews(session, cutoff)
        if expired == 0:
            logger.info(f"PII 保留期检查：无超期评价（早于 {cutoff:%Y-%m-%d}）")
            return 0

        if dry_run:
            logger.info(f"[dry-run] 将匿名化 {expired} 条超期评价的昵称哈希")
            return expired

        stmt = (
            update(Review)
            .where(Review.crawled_at < cutoff, Review.user_name_hash.isnot(None))
            .values(user_name_hash=None, user_name=None)
        )
        session.execute(stmt, execution_options={"synchronize_session": False})
        session.commit()
        logger.info(f"PII 保留期清理：已匿名化 {expired} 条评价（保留期 {days} 天）")
        return expired
    except Exception as e:
        session.rollback()
        logger.error(f"PII 保留期清理失败: {e}")
        raise
    finally:
        if owns_session:
            session.close()
