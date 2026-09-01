#!/usr/bin/env python3
"""插入测试数据，验证数据模型层全链路。

运行方式::

    python scripts/seed_test_data.py

前提条件: PostgreSQL 和数据库已启动（docker compose up -d）。
"""

import sys
import random
from datetime import datetime, timezone, timedelta
from decimal import Decimal

# 确保项目根目录在 sys.path 中
sys.path.insert(0, ".")

from models import (
    init_db,
    get_session,
    Product,
    ProductImage,
    ProductSKU,
    Review,
    PriceHistory,
    HotRanking,
    ReviewAnalysis,
    ImageAnalysis,
)


def _utcnow():
    return datetime.now(timezone.utc)


def seed(session):
    """插入测试数据。"""

    # ------------------------------------------------------------------
    # 商品 1 — 热销爆款
    # ------------------------------------------------------------------
    p1 = Product(
        platform="taobao",
        platform_id="taobao-test-001",
        name="800流明 USB充电自行车灯 夜骑装备 IPX6防水 超长续航",
        brand="BrightRide",
        shop_name="BrightRide旗舰店",
        category="自行车前灯",
        product_url="https://item.taobao.com/taobao-test-001.html",
        main_image_url="https://img.example.com/taobao-test-001-main.jpg",
        price=Decimal("89.00"),
        original_price=Decimal("129.00"),
        sales_volume=2345,
        comment_count=458,
        good_rate=Decimal("97.00"),
        specs={
            "流明": 800,
            "防水等级": "IPX6",
            "电池容量": "2000mAh",
            "充电方式": "USB-C",
            "安装方式": "车把快拆",
            "重量": "150g",
        },
        crawled_at=_utcnow(),
    )
    session.add(p1)

    # 商品 2 — 中端竞品
    p2 = Product(
        platform="taobao",
        platform_id="taobao-test-002",
        name="自行车灯前灯 强光远射 骑行手电筒 智能感应 送支架",
        brand="NightHawk",
        shop_name="NightHawk户外专营店",
        category="自行车前灯",
        product_url="https://item.taobao.com/taobao-test-002.html",
        main_image_url="https://img.example.com/taobao-test-002-main.jpg",
        price=Decimal("59.00"),
        original_price=Decimal("79.00"),
        sales_volume=3120,
        comment_count=892,
        good_rate=Decimal("94.50"),
        specs={
            "流明": 500,
            "防水等级": "IPX5",
            "电池容量": "1200mAh",
            "充电方式": "Micro-USB",
            "安装方式": "绑带式",
        },
        crawled_at=_utcnow(),
    )
    session.add(p2)

    # 商品 3 — 智能高端款
    p3 = Product(
        platform="taobao",
        platform_id="taobao-test-003",
        name="智能感应自行车灯 远近光自动切换 刹车警示灯 铝合金外壳",
        brand="SmartBike",
        shop_name="SmartBike官方店",
        category="自行车前灯",
        product_url="https://item.taobao.com/taobao-test-003.html",
        main_image_url="https://img.example.com/taobao-test-003-main.jpg",
        price=Decimal("159.00"),
        original_price=Decimal("199.00"),
        sales_volume=876,
        comment_count=203,
        good_rate=Decimal("98.20"),
        specs={
            "流明": 1200,
            "防水等级": "IPX7",
            "电池容量": "4000mAh",
            "充电方式": "USB-C快充",
            "安装方式": "车把夹装",
            "材质": "6061铝合金",
            "智能功能": "远近光自动切换/刹车感应",
        },
        crawled_at=_utcnow(),
    )
    session.add(p3)

    # 商品 4 — 低价走量款
    p4 = Product(
        platform="taobao",
        platform_id="taobao-test-004",
        name="自行车灯 LED强光 骑行灯 户外照明 防水车灯",
        brand="BasicGear",
        shop_name="骑迹户外小店",
        category="自行车前灯",
        product_url="https://item.taobao.com/taobao-test-004.html",
        main_image_url="https://img.example.com/taobao-test-004-main.jpg",
        price=Decimal("29.90"),
        original_price=Decimal("39.90"),
        sales_volume=5600,
        comment_count=2100,
        good_rate=Decimal("88.30"),
        specs={
            "流明": 200,
            "防水等级": "IPX4",
            "电池容量": "800mAh",
            "充电方式": "Micro-USB",
            "安装方式": "绑带式",
        },
        crawled_at=_utcnow(),
    )
    session.add(p4)

    # 商品 5 — 尾灯品类
    p5 = Product(
        platform="taobao",
        platform_id="taobao-test-005",
        name="自行车尾灯 USB充电 智能刹车感应 夜间警示 七彩变色",
        brand="BrightRide",
        shop_name="BrightRide旗舰店",
        category="自行车尾灯",
        product_url="https://item.taobao.com/taobao-test-005.html",
        main_image_url="https://img.example.com/taobao-test-005-main.jpg",
        price=Decimal("49.00"),
        original_price=Decimal("69.00"),
        sales_volume=1890,
        comment_count=567,
        good_rate=Decimal("95.80"),
        specs={
            "类型": "尾灯",
            "防水等级": "IPX5",
            "电池容量": "500mAh",
            "充电方式": "USB-C",
            "安装方式": "座管/座垫",
            "模式": "常亮/闪烁/呼吸/刹车感应",
        },
        crawled_at=_utcnow(),
    )
    session.add(p5)

    # ------------------------------------------------------------------
    # 图片（每个商品 2-3 张）
    # ------------------------------------------------------------------
    image_data = [
        (p1, "main", "https://img.example.com/p1-main.jpg", "data/images/1/main_1.jpg", 1),
        (p1, "detail", "https://img.example.com/p1-detail-1.jpg", "data/images/1/detail_1.jpg", 2),
        (p1, "detail", "https://img.example.com/p1-detail-2.jpg", "data/images/1/detail_2.jpg", 3),
        (p2, "main", "https://img.example.com/p2-main.jpg", "data/images/2/main_1.jpg", 1),
        (p2, "detail", "https://img.example.com/p2-detail-1.jpg", "data/images/2/detail_1.jpg", 2),
        (p3, "main", "https://img.example.com/p3-main.jpg", "data/images/3/main_1.jpg", 1),
        (p3, "detail", "https://img.example.com/p3-detail-1.jpg", "data/images/3/detail_1.jpg", 2),
        (p3, "detail", "https://img.example.com/p3-detail-2.jpg", "data/images/3/detail_2.jpg", 3),
        (p4, "main", "https://img.example.com/p4-main.jpg", "data/images/4/main_1.jpg", 1),
        (p5, "main", "https://img.example.com/p5-main.jpg", "data/images/5/main_1.jpg", 1),
    ]
    for product, img_type, url, path, order in image_data:
        session.add(ProductImage(
            product=product, image_type=img_type, image_url=url,
            local_path=path, sort_order=order,
        ))

    # ------------------------------------------------------------------
    # SKU（部分商品有多规格）
    # ------------------------------------------------------------------
    session.add(ProductSKU(
        product=p1, sku_name="标配版-800流明", price=Decimal("89.00"), stock=200,
        sku_specs={"版本": "标配", "流明": 800, "配件": "USB线+支架"},
    ))
    session.add(ProductSKU(
        product=p1, sku_name="高配版-1000流明", price=Decimal("119.00"), stock=150,
        sku_specs={"版本": "高配", "流明": 1000, "配件": "USB线+支架+头盔底座"},
    ))
    session.add(ProductSKU(
        product=p2, sku_name="标准版", price=Decimal("59.00"), stock=500,
        sku_specs={"版本": "标准", "流明": 500},
    ))

    # ------------------------------------------------------------------
    # 评价（每个商品 3-5 条）
    # ------------------------------------------------------------------
    reviews_data = [
        # p1 评价
        (p1, 5, "亮度非常满意，晚上照得很远，安全感满满。充一次电用了一个星期，续航很给力。安装也方便，不用工具就装好了。",
         "骑行爱好者小王", "plus会员", _utcnow() - timedelta(days=3), _utcnow() - timedelta(days=2)),
        (p1, 4, "整体不错，亮度够用，防水也靠谱，上次下雨骑回家没问题。就是支架稍有点松，过坑会往下掉一点。",
         "通勤老张", "普通用户", _utcnow() - timedelta(days=10), _utcnow() - timedelta(days=9)),
        (p1, 5, "第二次买了，之前那个用了两年才坏。亮度高，做工好，推荐给骑友。",
         "骑行天下", "plus会员", _utcnow() - timedelta(days=15), _utcnow() - timedelta(days=14)),
        (p1, 5, "每天下班8公里夜路，这个灯照得很清楚。体积小巧不占地方，性价比很高。",
         "城市夜骑侠", "普通用户", _utcnow() - timedelta(days=20), _utcnow() - timedelta(days=18)),

        # p2 评价
        (p2, 3, "价格便宜，亮度也还行，就是电池不太耐用，基本上两三天就要充一次。适合偶尔骑的。",
         "周末骑行者", "普通用户", _utcnow() - timedelta(days=5), _utcnow() - timedelta(days=4)),
        (p2, 5, "性价比超高！500流明通勤够用了，绑带安装也很方便，推荐给学生党。",
         "大学生小明", "普通用户", _utcnow() - timedelta(days=8), _utcnow() - timedelta(days=7)),
        (p2, 4, "用了三个月了，没啥大问题。就是充电口是Micro-USB，现在都用Type-C了，不太方便。",
         "数码控阿杰", "plus会员", _utcnow() - timedelta(days=25), _utcnow() - timedelta(days=24)),

        # p3 评价
        (p3, 5, "一分钱一分货，1200流明真的是夜如白昼！铝合金外壳手感超好，刹车感应功能很实用。",
         "装备党老李", "plus会员", _utcnow() - timedelta(days=2), _utcnow() - timedelta(days=1)),
        (p3, 5, "周末跟车队骑山路，弯道提前看到，队员都说这灯真亮。充一次电骑了两趟长途还有电。",
         "山地骑士", "普通用户", _utcnow() - timedelta(days=7), _utcnow() - timedelta(days=6)),
        (p3, 4, "功能很全，智能感应好用。就是价格确实贵了点，不过品质对得起这个价。",
         "理性消费者", "plus会员", _utcnow() - timedelta(days=12), _utcnow() - timedelta(days=11)),

        # p4 评价
        (p4, 2, "便宜没好货，亮度太暗了，夜骑完全不够用。买了就后悔，建议加钱买个好点的。",
         "后悔的买家", "普通用户", _utcnow() - timedelta(days=1), _utcnow() - timedelta(days=1)),
        (p4, 4, "这个价位已经不错了，日常买菜骑骑够用。别指望夜骑山路，市区有路灯的地方还行。",
         "买菜大叔", "普通用户", _utcnow() - timedelta(days=6), _utcnow() - timedelta(days=5)),
        (p4, 3, "中规中矩，用了一个月暂时没坏。充电口盖子有点松，防水不太放心。",
         "佛系用户", "普通用户", _utcnow() - timedelta(days=14), _utcnow() - timedelta(days=13)),

        # p5 评价
        (p5, 5, "尾灯很亮，七彩变色很酷，晚上骑车回头率高。刹车自动高亮这个功能很安全！",
         "潮流骑手", "普通用户", _utcnow() - timedelta(days=4), _utcnow() - timedelta(days=3)),
        (p5, 4, "安装简单，亮度够用。唯一的问题是续航稍短，高强度用两三天就得充电。",
         "夜骑达人", "plus会员", _utcnow() - timedelta(days=11), _utcnow() - timedelta(days=10)),
    ]

    for (product, rating, content, user, level, buy_date, review_date) in reviews_data:
        session.add(Review(
            product=product, platform_review_id=f"{product.platform_id}-r{random.randint(1000,9999)}",
            rating=rating, content=content, user_name=user, user_level=level,
            buy_date=buy_date, review_date=review_date, likes=random.randint(0, 50),
        ))

    # ------------------------------------------------------------------
    # 价格历史（最近7天）
    # ------------------------------------------------------------------
    for days_ago in range(7, 0, -1):
        d = (_utcnow() - timedelta(days=days_ago)).date()
        for p, base_price in [(p1, 89.0), (p2, 59.0), (p3, 159.0), (p4, 29.90), (p5, 49.0)]:
            # 随机小幅波动 ±5%
            fluctuation = 1 + random.uniform(-0.05, 0.05)
            session.add(PriceHistory(
                product=p,
                price=Decimal(str(round(base_price * fluctuation, 2))),
                snapshot_date=d,
            ))

    # ------------------------------------------------------------------
    # 热榜
    # ------------------------------------------------------------------
    rankings = [
        (1, "自行车灯热销榜", "weekly"), (2, "自行车灯热销榜", "weekly"),
        (3, "自行车灯热销榜", "weekly"), (4, "自行车灯热销榜", "weekly"),
        (1, "自行车灯好评榜", "weekly"), (3, "自行车灯好评榜", "weekly"),
        (5, "自行车灯好评榜", "weekly"),
    ]
    for rank_products, list_name, period in [
        ([p4, p2, p1, p5], "自行车灯热销榜", "weekly"),
        ([p3, p1, p5], "自行车灯好评榜", "weekly"),
    ]:
        for rank, prod in enumerate(rank_products, 1):
            session.add(HotRanking(
                product=prod, ranking=rank, list_name=list_name,
                period_type=period, fetched_at=_utcnow(),
            ))

    # ------------------------------------------------------------------
    # 评价分析结果（模拟 LLM 提取结果）
    # ------------------------------------------------------------------
    analysis_samples = [
        (  # p1 第一条评价
            [  # selling_points
                {"aspect": "亮度高", "mention": "晚上照得很远", "sentiment": "positive"},
                {"aspect": "续航久", "mention": "充一次电用了一个星期", "sentiment": "positive"},
                {"aspect": "安装方便", "mention": "不用工具就装好了", "sentiment": "positive"},
            ],
            [  # use_scenes
                {"scene": "夜间城市通勤", "detail": "每天下班8公里夜路", "confidence": 0.95},
            ],
            [],  # pain_points
            {  # user_profile
                "rider_type": "通勤",
                "bike_type": "山地车",
                "concern_priority": ["性价比", "性能优先"],
                "summary": "用户对亮度和续航非常满意，主要用于夜间城市通勤，注重性价比。"  # 把 summary 合并到这里
            },
            "用户对亮度和续航非常满意，主要用于夜间城市通勤，注重性价比。"  # 保留 summary
        ),
    ]

    # 将分析结果绑定到前几条评价
    all_reviews = session.query(Review).order_by(Review.id).all()
    for idx, (sp, sc, pp, up, summary) in enumerate(analysis_samples):
        if idx < len(all_reviews):
            session.add(ReviewAnalysis(
                review=all_reviews[idx],
                selling_points=sp,
                use_scenes=sc,
                pain_points=pp,
                user_profile=up,  # user_profile 里已经包含了 summary
                summary=up.get("summary", ""),  # 从 user_profile 中提取 summary
                analyzed_at=_utcnow(),
            ))

    # ------------------------------------------------------------------
    # 图片分析结果（模拟）
    # ------------------------------------------------------------------
    all_images = session.query(ProductImage).filter(ProductImage.image_type == "main").all()
    for img in all_images:
        session.add(ImageAnalysis(
            image=img,
            ocr_text="800流明超亮 USB充电 IPX6防水" if "001" in (img.image_url or "") else "",
            description=f"自行车灯主图，白底棚拍，{random.choice(['科技感','运动风','极简'])}风格",
            style_tags=[random.choice(["科技感", "运动风", "极简", "户外硬核"])],
            clip_vector_id=None,
            analyzed_at=_utcnow(),
        ))

    session.commit()
    print("✅ 测试数据插入完成！")


def main():
    print("🔧 初始化数据库表...")
    # 导入所有模型后再建表
    import models.product       # noqa: F401
    import models.review        # noqa: F401
    import models.price_history # noqa: F401
    import models.ranking       # noqa: F401
    import models.analysis      # noqa: F401

    init_db()
    print("✅ 数据库表已就绪")

    session = next(get_session())
    try:
        seed(session)
        # 验证
        product_count = session.query(Product).count()
        review_count = session.query(Review).count()
        print(f"\n📊 数据统计:")
        print(f"   商品: {product_count} 个")
        print(f"   评价: {review_count} 条")
        print(f"   图片: {session.query(ProductImage).count()} 张")
        print(f"   SKU:  {session.query(ProductSKU).count()} 个")
        print(f"   价格记录: {session.query(PriceHistory).count()} 条")
        print(f"   热榜: {session.query(HotRanking).count()} 条")
        print(f"   评价分析: {session.query(ReviewAnalysis).count()} 条")
        print(f"   图片分析: {session.query(ImageAnalysis).count()} 条")
    except Exception as e:
        session.rollback()
        print(f"❌ 错误: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
