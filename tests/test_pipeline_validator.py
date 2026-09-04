"""测试 DataPipeline 的数据校验逻辑（pipeline 校验）。"""

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.pipeline import DataValidator, DataPipeline
from crawler.schemas import ProductBrief


def _valid_product_data(**overrides):
    base = {
        "platform": "taobao",
        "platform_id": "123456",
        "name": "CBL1600PRO 自行车骑行灯 远射205米",
        "price": Decimal("391.00"),
        "good_rate": Decimal("97.00"),
    }
    base.update(overrides)
    return base


# ---------------- DataValidator.validate_product ----------------

def test_validate_product_ok():
    ok, msg = DataValidator.validate_product(_valid_product_data())
    assert ok is True
    assert msg == "ok"


def test_validate_product_short_name():
    ok, msg = DataValidator.validate_product(_valid_product_data(name="短"))
    assert ok is False
    assert "标题过短" in msg


def test_validate_product_empty_name():
    ok, _ = DataValidator.validate_product(_valid_product_data(name=""))
    assert ok is False


def test_validate_product_zero_price():
    ok, _ = DataValidator.validate_product(_valid_product_data(price=Decimal("0")))
    assert ok is False


def test_validate_product_negative_price():
    ok, _ = DataValidator.validate_product(_valid_product_data(price=Decimal("-5")))
    assert ok is False


def test_validate_product_too_high_price():
    ok, msg = DataValidator.validate_product(_valid_product_data(price=Decimal("10000")))
    assert ok is False
    assert "过高" in msg


def test_validate_product_invalid_price_string():
    ok, _ = DataValidator.validate_product(_valid_product_data(price="不是价格"))
    assert ok is False


def test_validate_product_unknown_platform():
    ok, msg = DataValidator.validate_product(_valid_product_data(platform="jd"))
    assert ok is False
    assert "未知平台" in msg


def test_validate_product_missing_platform_id():
    ok, msg = DataValidator.validate_product(_valid_product_data(platform_id=""))
    assert ok is False
    assert "platform_id" in msg


def test_validate_product_good_rate_out_of_range():
    ok, _ = DataValidator.validate_product(_valid_product_data(good_rate=Decimal("120")))
    assert ok is False
    ok2, _ = DataValidator.validate_product(_valid_product_data(good_rate=Decimal("-1")))
    assert ok2 is False


# ---------------- DataValidator.validate_review ----------------

def test_validate_review_ok():
    ok, msg = DataValidator.validate_review({"rating": 5, "content": "灯很亮值得买"})
    assert ok is True


def test_validate_review_bad_rating():
    assert DataValidator.validate_review({"rating": 0, "content": "差评"})[0] is False
    assert DataValidator.validate_review({"rating": 6, "content": "好评"})[0] is False


def test_validate_review_short_content():
    ok, _ = DataValidator.validate_review({"rating": 5, "content": "好"})
    assert ok is False


# ---------------- save_product 校验网关 ----------------

def test_save_product_validation_failure_returns_none(monkeypatch):
    """校验失败时 save_product 应直接返回 (None, False)，不触达数据库。"""
    pipeline = DataPipeline()

    fake_session = MagicMock()

    def _fake_get_session():
        yield fake_session

    monkeypatch.setattr("crawler.pipeline.get_session", _fake_get_session)

    # 标题过短 → 应在校验阶段被拒
    bad_brief = ProductBrief(
        platform="taobao",
        platform_id="123",
        name="短",
        price=Decimal("39.9"),
        shop_name="某店",
        main_image_url="https://img.alicdn.com/x.jpg",
        sales_volume=10,
        comment_count=1,
    )
    product_id, is_new = pipeline.save_product(bad_brief, None)
    assert product_id is None
    assert is_new is False
    # 不应调用任何数据库写入
    fake_session.commit.assert_not_called()
    fake_session.add.assert_not_called()
