"""爬虫主调度器 — 串联搜索→详情→评价→入库→图片下载→图片记录同步全流程。

CrawlerEngine 是爬虫模块的顶层入口，负责：
- 按关键词调用 spider.search() 获取商品列表
- 逐个调用 spider.get_detail() 抓取详情
- 逐个调用 spider.get_reviews() 采集评价
- 通过 DataPipeline 清洗、校验、去重、入库
- 收集所有图片下载任务，统一交给 ImageDownloader 执行
- 下载完成后同步写入 product_images 表
- 输出 CrawlReport 汇总本次采集结果

支持断点续爬：通过 retry.Checkpoint 记录已处理的 platform_id，
程序崩溃后重启可跳过已处理的商品。

Usage::

    from crawler.engine import CrawlerEngine

    engine = CrawlerEngine()
    report = await engine.run(
        keywords=["自行车灯"],
        max_products_per_keyword=50,
        max_review_pages=3,
        download_images=True,
    )
    print(f"新增: {report.products_new}, 评价: {report.reviews_collected}")
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import List, Optional

from loguru import logger

from config.settings import settings
from crawler.base import BaseSpider
from crawler.taobao_spider import TaobaoSpider
from crawler.pipeline import DataPipeline
from crawler.downloader import ImageDownloader
from crawler.retry import Checkpoint, with_retry
from crawler.schemas import CrawlReport
from crawler.compliance import RateLimiter, RobotsChecker

# 各平台搜索入口（用于 robots.txt 检查与限速的 URL 取样）
PLATFORM_SEARCH_URL = {
    "taobao": "https://main.m.taobao.com/search",
    "pdd": "https://mobile.yangkeduo.com/search_result.html",
}


def _utcnow():
    return datetime.now(timezone.utc)


class CrawlerEngine:
    """爬虫主调度器 — 管理多平台爬虫、数据管线、图片下载。

    参数:
        spiders:       平台爬虫列表，默认仅 [TaobaoSpider()]
        pipeline:      数据处理管线
        downloader:    图片下载器
        concurrency:   同时处理的商品数（详情抓取并发度）
        enable_checkpoint: 是否启用断点续爬
    """

    def __init__(
        self,
        spiders: List[BaseSpider] = None,
        pipeline: DataPipeline = None,
        downloader: ImageDownloader = None,
        concurrency: int = 3,
        enable_checkpoint: bool = True,
    ):
        self.spiders = spiders or [TaobaoSpider()]
        self.pipeline = pipeline or DataPipeline()
        self.downloader = downloader or ImageDownloader(concurrency=5)
        self.concurrency = concurrency
        self.enable_checkpoint = enable_checkpoint

        # 信号量控制详情抓取并发（惰性创建：构造期还没有事件循环）
        self._detail_semaphore: Optional[asyncio.Semaphore] = None

        # 合规闸门：robots.txt 遵从 + 全局限速（默认开启，见 config/settings.py）
        self._limiter = RateLimiter(
            rate_per_minute=settings.crawler_rate_per_minute, burst=2
        )
        self._robots = RobotsChecker(respect=settings.crawler_respect_robots)

    # ==================================================================
    # 合规闸门
    # ==================================================================

    def _get_semaphore(self) -> asyncio.Semaphore:
        """惰性获取并发信号量（在运行中的事件循环内创建）。"""
        if self._detail_semaphore is None:
            self._detail_semaphore = asyncio.Semaphore(self.concurrency)
        return self._detail_semaphore

    async def _guard(self, url: str) -> bool:
        """请求前的合规检查：robots 允许 → 限速等待。

        返回 False 表示被 robots.txt 拒绝，调用方应跳过该请求。
        """
        if not await self._robots.allowed(url):
            logger.warning(f"robots.txt 禁止访问，跳过: {url}")
            return False
        await self._limiter.acquire()
        return True

    # ==================================================================
    # 主入口
    # ==================================================================

    async def run(
        self,
        keywords: List[str],
        max_products_per_keyword: int = 100,
        max_review_pages: int = 5,
        download_images: bool = True,
    ) -> CrawlReport:
        """执行完整的采集流程。

        流程:
        1. 每个 spider → search(keyword) → 商品列表
        2. 每个商品 → get_detail() → 规格参数 (并发受 concurrency 控制)
        3. 每个商品 → get_reviews() → 评价数据 (串行，避免风控)
        4. pipeline 清洗→校验→去重→入库
        5. 图片异步下载
        6. 下载成功的图片记录同步写入 product_images 表
        7. 返回 CrawlReport

        参数:
            keywords:                 搜索关键词列表，如 ["自行车灯", "自行车尾灯"]
            max_products_per_keyword: 每个关键词最多采集的商品数
            max_review_pages:         每个商品最多抓取几页评价
            download_images:          是否下载图片

        返回:
            CrawlReport 汇总报告
        """
        started_at = _utcnow()
        t0 = time.time()

        logger.info(
            f"🚀 爬虫启动: keywords={keywords}, "
            f"max_products={max_products_per_keyword}, "
            f"spiders={[s.platform for s in self.spiders]}"
        )

        report = CrawlReport(
            keyword=",".join(keywords),
            started_at=started_at,
        )
        self.pipeline.reset_stats()

        try:
            # ---- Phase 1: 搜索 ----
            # 存 (spider, brief) 二元组：多平台时 Phase 2 才能找到正确的 spider，
            # 而不是误用 Phase 1 循环结束后残留的外层 spider 变量
            all_briefs: list = []
            for spider in self.spiders:
                try:
                    await spider.setup()
                except Exception as e:
                    logger.error(f"Spider 初始化失败 [{spider.platform}]: {e}")
                    report.errors.append(f"setup[{spider.platform}]: {e}")
                    continue

                # 合规闸门：robots 检查 + 限速（搜索入口按平台取样）
                search_url = PLATFORM_SEARCH_URL.get(spider.platform)
                if search_url and not await self._guard(search_url):
                    logger.error(
                        f"[{spider.platform}] 被 robots.txt 拒绝，跳过该平台。"
                        f"如你已获得授权，可设置 BKL_CRAWLER_RESPECT_ROBOTS=false"
                    )
                    report.errors.append(f"robots_denied[{spider.platform}]")
                    continue

                for keyword in keywords:
                    try:
                        if not await self._guard(search_url or ""):
                            break
                        briefs = await with_retry(
                            lambda kw=keyword: spider.search(kw, max_items=max_products_per_keyword),
                            max_retries=2,
                            base_delay=5.0,
                        )
                        report.products_found += len(briefs)
                        all_briefs.extend((spider, b) for b in briefs)
                        logger.info(f"搜索 '{keyword}' → {len(briefs)} 个商品")
                    except Exception as e:
                        logger.error(f"搜索失败 [{spider.platform}][{keyword}]: {e}")
                        report.errors.append(f"search[{spider.platform}][{keyword}]: {e}")

            if not all_briefs:
                logger.warning("未搜索到任何商品，爬虫结束")
                return report

            # 断点续爬: 跳过已处理的（task_id 按关键词稳定命名，重启后可真正续爬）
            checkpoint = Checkpoint(f"crawl_{'_'.join(keywords)}")
            pending = checkpoint.skip_done(
                all_briefs, key_fn=lambda pair: pair[1].platform_id
            )

            logger.info(f"Phase 1 完成: {len(pending)}/{len(all_briefs)} 个商品待处理")

            # ---- Phase 2: 详情 + 评价 (逐个处理) ----
            for i, (spider, brief) in enumerate(pending):
                if getattr(spider, '_captcha_triggered', False):
                    logger.warning("已触发验证码，停止后续商品的抓取")
                    report.captcha_triggered = True
                    break

                logger.info(f"[{i + 1}/{len(pending)}] 处理: {brief.name[:50]}...")

                # 商品间延迟（降低风控风险）
                if i > 0:
                    delay = 15 + (i % 5) * 5  # 15~35 秒随机梯度
                    logger.debug(f"等待 {delay}s 后处理下一个商品...")
                    await asyncio.sleep(delay)

                product_id = None

                # 详情
                detail = None
                try:
                    if not await self._guard(brief.product_url or ""):
                        logger.warning(f"跳过被 robots 拒绝的商品: {brief.platform_id}")
                        continue
                    detail = await self._fetch_detail_with_limit(spider, brief)
                except Exception as e:
                    logger.error(f"详情抓取异常 [{brief.platform_id}]: {e}")
                    report.errors.append(f"detail[{brief.platform_id}]: {e}")

                # 入库
                try:
                    product_id, is_new = self.pipeline.save_product(brief, detail)
                    if is_new:
                        report.products_new += 1
                    else:
                        report.products_updated += 1
                except Exception as e:
                    logger.error(f"商品入库失败 [{brief.platform_id}]: {e}")
                    report.errors.append(f"save_product[{brief.platform_id}]: {e}")
                    continue

                # 评价
                if product_id:
                    try:
                        reviews = await with_retry(
                            lambda: spider.get_reviews(brief, max_pages=max_review_pages),
                            max_retries=1,
                            base_delay=5.0,
                        )
                        saved = self.pipeline.save_reviews(product_id, reviews)
                        report.reviews_collected += saved
                    except Exception as e:
                        logger.error(f"评价采集异常 [{brief.platform_id}]: {e}")
                        report.errors.append(f"reviews[{brief.platform_id}]: {e}")

                    # 价格快照
                    try:
                        self.pipeline.save_price_snapshot(product_id, brief.price)
                    except Exception as e:
                        logger.debug(f"价格快照写入跳过 [{brief.platform_id}]: {e}")

                # 标记已处理
                checkpoint.mark_done(brief.platform_id)

            # ---- Phase 3: 图片下载 ----
            if download_images:
                image_tasks = self.pipeline.get_image_tasks()
                if image_tasks:
                    logger.info(f"开始下载 {len(image_tasks)} 张图片...")
                    dl_report = await self.downloader.download_batch(image_tasks)
                    report.images_downloaded = dl_report["success"]
                    report.images_failed = dl_report["failed"]

                    # 将下载成功的图片记录写入 product_images 表
                    try:
                        synced = self.downloader.sync_product_images(
                            image_tasks, dl_report.get("paths", [])
                        )
                        logger.info(f"图片记录同步完成: {synced} 条入库")
                    except Exception as e:
                        logger.error(f"图片记录同步失败: {e}")
                        report.errors.append(f"sync_product_images: {e}")

            # ---- Phase 4: 清理 ----
            for spider in self.spiders:
                try:
                    await spider.teardown()
                except Exception as e:
                    logger.error(f"Spider 清理失败 [{spider.platform}]: {e}")

        except Exception as e:
            logger.error(f"爬虫执行异常: {e}")
            report.errors.append(f"fatal: {e}")

        report.finished_at = _utcnow()
        report.duration_seconds = time.time() - t0

        # 打印摘要
        logger.info(
            f"🏁 爬虫完成 [{report.duration_seconds:.0f}s]: "
            f"搜索 {report.products_found} → "
            f"新增 {report.products_new} / 更新 {report.products_updated} → "
            f"评价 {report.reviews_collected} 条 → "
            f"图片 {report.images_downloaded}/{report.images_downloaded + report.images_failed} → "
            f"错误 {len(report.errors)} 个"
        )

        return report

    # ==================================================================
    # 内部方法
    # ==================================================================

    async def _fetch_detail_with_limit(
        self, spider: BaseSpider, brief
    ):
        """带并发控制的详情抓取。"""
        async with self._get_semaphore():
            return await spider.get_detail(brief)
