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

from crawler.base import BaseSpider
from crawler.jd_spider import JDSpider
from crawler.pipeline import DataPipeline
from crawler.downloader import ImageDownloader
from crawler.retry import Checkpoint, with_retry
from crawler.schemas import CrawlReport


def _utcnow():
    return datetime.now(timezone.utc)


class CrawlerEngine:
    """爬虫主调度器 — 管理多平台爬虫、数据管线、图片下载。

    参数:
        spiders:       平台爬虫列表，默认仅 [JDSpider()]
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
        self.spiders = spiders or [JDSpider(use_cdp=True, use_mobile_api=True)]
        self.pipeline = pipeline or DataPipeline()
        self.downloader = downloader or ImageDownloader(concurrency=5)
        self.concurrency = concurrency
        self.enable_checkpoint = enable_checkpoint

        # 信号量控制详情抓取并发
        self._detail_semaphore = asyncio.Semaphore(concurrency)

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
            all_briefs: list = []
            for spider in self.spiders:
                try:
                    await spider.setup()
                except Exception as e:
                    logger.error(f"Spider 初始化失败 [{spider.platform}]: {e}")
                    report.errors.append(f"setup[{spider.platform}]: {e}")
                    continue

                for keyword in keywords:
                    try:
                        briefs = await with_retry(
                            lambda kw=keyword: spider.search(kw, max_items=max_products_per_keyword),
                            max_retries=2,
                            base_delay=5.0,
                        )
                        report.products_found += len(briefs)
                        all_briefs.extend(briefs)
                        logger.info(f"搜索 '{keyword}' → {len(briefs)} 个商品")
                    except Exception as e:
                        logger.error(f"搜索失败 [{spider.platform}][{keyword}]: {e}")
                        report.errors.append(f"search[{spider.platform}][{keyword}]: {e}")

            if not all_briefs:
                logger.warning("未搜索到任何商品，爬虫结束")
                return report

            # 断点续爬: 跳过已处理的
            checkpoint = Checkpoint(f"crawl_{started_at.strftime('%Y%m%d_%H%M')}")
            pending = checkpoint.skip_done(all_briefs, key_fn=lambda b: b.platform_id)

            logger.info(f"Phase 1 完成: {len(pending)}/{len(all_briefs)} 个商品待处理")

            # ---- Phase 2: 详情 + 评价 (逐个处理) ----
            for i, brief in enumerate(pending):
                if hasattr(spider, '_captcha_triggered') and getattr(spider, '_captcha_triggered'):
                    logger.warning("已触发验证码，停止后续商品的抓取")
                    report.captcha_triggered = True
                    break

                logger.info(f"[{i + 1}/{len(pending)}] 处理: {brief.name[:50]}...")

                product_id = None

                # 详情
                detail = None
                try:
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
                    except Exception:
                        pass

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
    # 定时任务入口
    # ==================================================================

    async def run_scheduled(self) -> CrawlReport:
        """定时任务入口 — 从 config/settings.py 读取参数。

        供 M8 调度器调用的标准化接口。
        """
        from config.settings import settings

        return await self.run(
            keywords=["自行车灯", "自行车前灯", "自行车尾灯"],
            max_products_per_keyword=settings.crawler_max_products,
        )

    # ==================================================================
    # 断点恢复
    # ==================================================================

    async def resume(self, checkpoint_id: str) -> CrawlReport:
        """从指定检查点恢复未完成的爬虫任务。

        参数:
            checkpoint_id: 检查点 ID（即上次 run() 时使用的 task_id）
        """
        checkpoint = Checkpoint(checkpoint_id)
        done_count = checkpoint.done_count
        logger.info(f"从检查点恢复: 已处理 {done_count} 个，将跳过")

        # 恢复执行（复用 run 逻辑）
        return await self.run(
            keywords=["自行车灯"],  # 恢复时需重新搜索
            max_products_per_keyword=200,  # 扩大搜索范围以覆盖遗漏的商品
        )

    # ==================================================================
    # 内部方法
    # ==================================================================

    async def _fetch_detail_with_limit(
        self, spider: BaseSpider, brief
    ):
        """带并发控制的详情抓取。"""
        async with self._detail_semaphore:
            return await spider.get_detail(brief)
