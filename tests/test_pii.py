"""PII 脱敏测试 — 昵称哈希化 + 保留期匿名化。

对应审计 P2#15：用户昵称（个人信息）不得明文落库，且需有保留期清理机制。
数据库部分用 SQLite 内存库承载 reviews 表，避免依赖 Postgres。
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from crawler.pii import hash_user_name, anonymize_expired_reviews
from models import Review


# SQLite 不会自增 BigInteger 主键，这里手写 DDL 建一个同构的 reviews 表。
# 只建 reviews（products 含 JSONB，SQLite 方言不支持）。
_REVIEWS_DDL = """
CREATE TABLE reviews (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id         INTEGER,
    platform_review_id TEXT,
    rating             INTEGER NOT NULL,
    content            TEXT,
    user_name          TEXT,
    user_name_hash     TEXT,
    user_level         TEXT,
    buy_date           DATETIME,
    review_date        DATETIME,
    likes              INTEGER,
    reply_content      TEXT,
    crawled_at         DATETIME
)
"""


def _make_session():
    """创建只含 reviews 表的 SQLite 内存会话。"""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(_REVIEWS_DDL)
    return sessionmaker(bind=engine)()


def _add_review(session, *, hash_value, crawled_at, user_name=None):
    rv = Review(
        product_id=1,
        platform_review_id=f"r-{abs(hash(str(crawled_at)))}",
        rating=5,
        content="灯很亮，续航也够用",
        user_name=user_name,
        user_name_hash=hash_value,
        crawled_at=crawled_at,
    )
    session.add(rv)
    session.commit()
    return rv


# ---------------------------------------------------------------------------
# 哈希
# ---------------------------------------------------------------------------

def test_hash_is_deterministic():
    assert hash_user_name("t**8", pepper="p1") == hash_user_name("t**8", pepper="p1")


def test_hash_is_sha256_hex64():
    h = hash_user_name("t**8", pepper="p1")
    assert len(h) == 64
    int(h, 16)  # 全为十六进制字符，否则抛 ValueError


def test_hash_varies_with_pepper():
    """pepper 不同则结果不同 —— 保证拿到库也无法反推昵称。"""
    assert hash_user_name("t**8", pepper="p1") != hash_user_name("t**8", pepper="p2")


def test_hash_varies_with_input():
    assert hash_user_name("a", pepper="p1") != hash_user_name("b", pepper="p1")


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_hash_blank_returns_none(value):
    """空白昵称不写库（返回 None 而不是空串哈希）。"""
    assert hash_user_name(value, pepper="p1") is None


# ---------------------------------------------------------------------------
# 保留期匿名化
# ---------------------------------------------------------------------------

def test_anonymize_clears_expired_hash_only():
    session = _make_session()
    now = datetime.now(timezone.utc)
    old = _add_review(session, hash_value="a" * 64, crawled_at=now - timedelta(days=400))
    fresh = _add_review(session, hash_value="b" * 64, crawled_at=now - timedelta(days=10))

    affected = anonymize_expired_reviews(retention_days=365, session=session)

    assert affected == 1
    session.refresh(old)
    session.refresh(fresh)
    assert old.user_name_hash is None      # 超期 → 匿名化
    assert fresh.user_name_hash == "b" * 64  # 未超期 → 保留


def test_anonymize_clears_legacy_plaintext():
    """历史遗留的 user_name 明文也一并清除。"""
    session = _make_session()
    rv = _add_review(
        session,
        hash_value="c" * 64,
        crawled_at=datetime.now(timezone.utc) - timedelta(days=500),
        user_name="张三",
    )

    anonymize_expired_reviews(retention_days=365, session=session)

    session.refresh(rv)
    assert rv.user_name is None
    assert rv.user_name_hash is None


def test_anonymize_keeps_review_content():
    """匿名化只动 PII 字段，评价正文/评分必须原样保留。"""
    session = _make_session()
    rv = _add_review(
        session,
        hash_value="d" * 64,
        crawled_at=datetime.now(timezone.utc) - timedelta(days=500),
    )

    anonymize_expired_reviews(retention_days=365, session=session)

    session.refresh(rv)
    assert rv.content == "灯很亮，续航也够用"
    assert rv.rating == 5


def test_anonymize_dry_run_does_not_modify():
    session = _make_session()
    rv = _add_review(
        session,
        hash_value="e" * 64,
        crawled_at=datetime.now(timezone.utc) - timedelta(days=500),
    )

    affected = anonymize_expired_reviews(
        retention_days=365, session=session, dry_run=True
    )

    assert affected == 1
    session.refresh(rv)
    assert rv.user_name_hash == "e" * 64  # dry-run 不落库


def test_anonymize_disabled_when_retention_zero():
    session = _make_session()
    _add_review(
        session,
        hash_value="f" * 64,
        crawled_at=datetime.now(timezone.utc) - timedelta(days=5000),
    )
    assert anonymize_expired_reviews(retention_days=0, session=session) == 0


def test_anonymize_no_expired_rows():
    session = _make_session()
    _add_review(
        session,
        hash_value="g" * 64,
        crawled_at=datetime.now(timezone.utc),
    )
    assert anonymize_expired_reviews(retention_days=365, session=session) == 0
