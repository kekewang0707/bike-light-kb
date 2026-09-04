"""测试商品卡榜单数据 → HotRanking 字段构造（rank 提升纯函数）。"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.pipeline import build_hot_ranking_fields


def test_build_hot_ranking_fields():
    rank = {
        "text": "自行车灯热销榜·第2名",
        "url": "https://pages-fast.m.taobao.com/x",
        "no": 2,
        "rank_type": "tmall",
    }
    fetched = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    fields = build_hot_ranking_fields(42, rank, fetched)
    assert fields["product_id"] == 42
    assert fields["ranking"] == 2
    assert fields["list_name"] == "自行车灯热销榜·第2名"
    assert fields["period_type"] == "daily"
    assert fields["fetched_at"] == fetched


def test_build_hot_ranking_fields_no_text():
    fields = build_hot_ranking_fields(7, {"no": 1}, None)
    assert fields["ranking"] == 1
    assert fields["list_name"] == ""
    assert fields["period_type"] == "daily"
