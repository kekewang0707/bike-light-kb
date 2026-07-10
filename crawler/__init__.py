"""自行车灯电商知识库 — 爬虫引擎模块。

提供多平台电商数据采集能力，支持 API/Playwright/HTML 三级降级。

核心组件:
- CrawlerEngine: 爬虫主调度器（搜索→详情→评价→图片→入库）
- JDSpider:      京东平台爬虫（API优先 + CDP连接 + XHR拦截）
- BaseSpider:    爬虫抽象基类（扩展新平台时继承此基类）
- DataPipeline:  数据处理管线（清洗/校验/去重/入库）
- ImageDownloader: 异步图片下载器
- AntiDetect:    7层反爬对抗体系
- Checkpoint:    断点续爬支持

使用示例::

    from crawler import CrawlerEngine

    engine = CrawlerEngine()
    report = await engine.run(
        keywords=["自行车灯"],
        max_products_per_keyword=50,
        download_images=True,
    )

    print(f"新增商品: {report.products_new}")
    print(f"采集评价: {report.reviews_collected}")
    print(f"下载图片: {report.images_downloaded}")
"""

# ---- 数据结构 ----
from crawler.schemas import (
    ProductBrief,
    ProductDetail,
    SkuData,
    ReviewData,
    ImageTask,
    CrawlReport,
    PricePoint,
)

# ---- 引擎 ----
from crawler.engine import CrawlerEngine

# ---- 平台爬虫 ----
from crawler.base import BaseSpider
from crawler.jd_spider import JDSpider
from crawler.taobao_spider import TaobaoSpider

# ---- 管线 ----
from crawler.pipeline import DataPipeline, DataValidator

# ---- 工具 ----
from crawler.downloader import ImageDownloader
from crawler.parser import parse_price, parse_int, clean_html
from crawler.retry import with_retry, Checkpoint
from crawler.cookie_manager import CookieManager
from crawler.proxy_pool import ProxyPool

# ---- 反爬 ----
from crawler.anti_detect import AntiDetect, CDPConnection, LoginHelper

__all__ = [
    # 引擎
    "CrawlerEngine",
    # 平台爬虫
    "BaseSpider",
    "JDSpider",
    "TaobaoSpider",
    # 数据结构
    "ProductBrief",
    "ProductDetail",
    "SkuData",
    "ReviewData",
    "ImageTask",
    "CrawlReport",
    "PricePoint",
    # 管线
    "DataPipeline",
    "DataValidator",
    # 工具
    "ImageDownloader",
    "parse_price",
    "parse_int",
    "clean_html",
    "with_retry",
    "Checkpoint",
    "CookieManager",
    "ProxyPool",
    # 反爬
    "AntiDetect",
    "CDPConnection",
    "LoginHelper",
]
