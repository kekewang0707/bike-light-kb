"""测试 TaobaoSpider 的商品卡字段解析（含 original_price 边界）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.taobao_spider import TaobaoSpider


SAMPLE_ITEM = {
    "title": "CBL1600PRO自行车骑行灯 远射205米磁吸 航空铝外壳",
    "price": "419.00",
    "realSales": "全网6000+人付款",
    "priceShowWithIcon": {
        "price": "391.00",
        "originPrice": "￥419",
        "suffixText": "首单价",
        "priceColor": "#ff5000",
        "unit": "¥",
    },
    "iconUspSortInfo": [
        {"usp_code": "numericalCommentInfo", "text": '"18人评价"质感很棒"'},
        {"usp_code": "tmRankInfo", "text": "自行车灯热销榜·第2名",
         "url": "https://pages-fast.m.taobao.com/x", "rankType": "tmall"},
        {"usp_code": "extend_source", "text": "近7天3000+人逛过"},
    ],
    "specialUSPInfo": [
        {"usp_code": "xinxiang:xinxiangDiscountSpeaker", "text": "品牌新客补贴，当日有效",
         "icon": "https://gw.alicdn.com/icon.png"},
    ],
    "shopDiscountInfo": "品牌新客补贴，当日有效",
}


def test_parse_card_fields_full():
    card = TaobaoSpider._parse_card_fields(SAMPLE_ITEM)
    assert card["price"] == 391.0
    assert card["original_price"] == 419.0
    assert card["sales_volume"] == 6000
    assert card["comment_count"] == 18
    assert card["comment_snippet"] == "质感很棒"
    assert card["rank"] == {
        "text": "自行车灯热销榜·第2名",
        "url": "https://pages-fast.m.taobao.com/x",
        "no": 2,
        "rank_type": "tmall",
    }
    assert card["popularity_text"] == "近7天3000+人逛过"
    assert card["marketing_usp"][0]["code"] == "xinxiang:xinxiangDiscountSpeaker"
    assert card["shop_discount"] == "品牌新客补贴，当日有效"


def test_parse_card_fields_no_origin_price():
    """无划线价时 original_price 必须为 None（不回退到当前价）。"""
    item = {"priceShowWithIcon": {"price": "391.00"}, "realSales": "100人付款"}
    card = TaobaoSpider._parse_card_fields(item)
    assert card["original_price"] is None
    assert card["price"] == 391.0


def test_parse_card_fields_empty():
    card = TaobaoSpider._parse_card_fields({})
    assert card["price"] == 0.0
    assert card["original_price"] is None
    assert card["rank"] is None


def test_extract_rank_no():
    assert TaobaoSpider._extract_rank_no("自行车灯热销榜·第2名") == 2
    assert TaobaoSpider._extract_rank_no("第10名") == 10
    assert TaobaoSpider._extract_rank_no("无榜单信息") is None
    assert TaobaoSpider._extract_rank_no("") is None


def test_parse_int():
    assert TaobaoSpider._parse_int("全网6000+人付款") == 6000
    assert TaobaoSpider._parse_int("1.2万人逛过") == 12000
    assert TaobaoSpider._parse_int("无销量") == 0
