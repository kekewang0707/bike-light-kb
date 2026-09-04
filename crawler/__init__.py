"""自行车灯电商知识库 — 爬虫引擎模块。

提供多平台电商数据采集能力，支持 API/Playwright/HTML 三级降级。

核心组件:
- CrawlerEngine: 爬虫主调度器（搜索→详情→评价→图片→入库）
- TaobaoSpider:  淘宝/天猫平台爬虫（移动端API优先 + 反爬降级）
- BaseSpider:    爬虫抽象基类（扩展新平台时继承此基类）
- DataPipeline:  数据处理管线（清洗/校验/去重/入库）
- ImageDownloader: 异步图片下载器
- AntiDetect:    反爬对抗体系
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
    ReviewData,
    ImageTask,
    CrawlReport,
)

# ---- 引擎 ----
from crawler.engine import CrawlerEngine

# ---- 平台爬虫 ----
from crawler.base import BaseSpider
from crawler.taobao_spider import TaobaoSpider

# ---- 管线 ----
from crawler.pipeline import DataPipeline, DataValidator

# ---- 工具 ----
from crawler.downloader import ImageDownloader
from crawler.retry import with_retry, Checkpoint
from crawler.cookie_manager import CookieManager

# ---- 淘宝登录 / Cookie ----
from crawler.taobao_auth import login_and_save, load_cookies, cookie_dict, request, check_login

# ---- mtop 签名请求 ----
from crawler.mtop import MtopClient, search as mtop_search

# ---- 反爬 ----
from crawler.anti_detect import AntiDetect

__all__ = [
    # 引擎
    "CrawlerEngine",
    # 平台爬虫
    "BaseSpider",
    "TaobaoSpider",
    # 数据结构
    "ProductBrief",
    "ProductDetail",
    "ReviewData",
    "ImageTask",
    "CrawlReport",
    # 管线
    "DataPipeline",
    "DataValidator",
    # 工具
    "ImageDownloader",
    "with_retry",
    "Checkpoint",
    "CookieManager",
    # 淘宝登录 / Cookie
    "login_and_save",
    "load_cookies",
    "cookie_dict",
    "request",
    "check_login",
    # mtop 签名请求
    "MtopClient",
    "mtop_search",
    # 反爬
    "AntiDetect",
    "CDPConnection",
    "LoginHelper",
]
