"""重试策略 — 指数退避 + 断点续爬。

提供两种重试模式：
- with_retry: 函数级重试（指数退避）
- 断点续爬: 将已处理的商品 ID 持久化，重启后跳过

Usage::

    from crawler.retry import with_retry, Checkpoint

    # 函数级重试
    result = await with_retry(
        lambda: spider.search("自行车灯"),
        max_retries=3,
    )

    # 断点续爬
    checkpoint = Checkpoint("task_001")
    for product in products:
        if checkpoint.is_done(product.platform_id):
            continue
        await process(product)
        checkpoint.mark_done(product.platform_id)
"""

import asyncio
import json
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Any, Set
from functools import wraps

from loguru import logger


# ============================================================================
# 指数退避重试
# ============================================================================

async def with_retry(
    fn: Callable,
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    exponential: bool = True,
    jitter: bool = True,
    retryable_exceptions: tuple = (Exception,),
) -> Any:
    """指数退避重试装饰器（async 版本）。

    参数:
        fn: 要重试的异步函数（无参数）
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟上限（秒）
        exponential: True 使用指数退避，False 使用固定延迟
        jitter: True 添加随机抖动（避免惊群效应）
        retryable_exceptions: 哪些异常类型触发重试

    返回:
        函数成功时的返回值

    异常:
        重试耗尽后抛出最后一次的异常

    延迟公式:
        delay = min(base_delay * (2 ** attempt), max_delay)  [指数退避]
        delay *= random.uniform(0.5, 1.5)                    [抖动]
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            result = await fn()
            if attempt > 0:
                logger.info(f"第 {attempt} 次重试成功")
            return result
        except retryable_exceptions as e:
            last_exception = e
            if attempt >= max_retries:
                logger.error(f"重试 {max_retries} 次后仍失败: {e}")
                raise

            # 计算延迟
            if exponential:
                delay = min(base_delay * (2 ** attempt), max_delay)
            else:
                delay = base_delay

            if jitter:
                delay *= random.uniform(0.5, 1.5)

            logger.warning(
                f"第 {attempt + 1}/{max_retries} 次重试失败: {e}，"
                f"{delay:.1f}s 后重试..."
            )
            await asyncio.sleep(delay)


def retry(max_retries: int = 3, base_delay: float = 2.0):
    """同步函数的重试装饰器。"""
    def decorator(fn: Callable):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    result = fn(*args, **kwargs)
                    if attempt > 0:
                        logger.info(f"{fn.__name__} 第 {attempt} 次重试成功")
                    return result
                except Exception as e:
                    last_exception = e
                    if attempt >= max_retries:
                        raise
                    delay = base_delay * (2 ** attempt)
                    delay *= random.uniform(0.5, 1.5)
                    logger.warning(
                        f"{fn.__name__} 重试 {attempt + 1}/{max_retries}: {e}，"
                        f"{delay:.1f}s 后重试"
                    )
                    time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator


# ============================================================================
# 断点续爬
# ============================================================================

class Checkpoint:
    """断点续爬检查点 — 记录已处理的 platform_id。

    原理:
    1. 每次处理完一个商品，将其 platform_id 写入 checkpoint 文件
    2. 下次启动时加载，跳过已处理的 ID
    3. 避免因程序崩溃导致从头开始

    Usage::

        cp = Checkpoint("task_crawl_jd_20260709")
        for product in products:
            if cp.is_done(product.platform_id):
                continue
            try:
                await process(product)
                cp.mark_done(product.platform_id)
            except Exception as e:
                logger.error(f"处理失败: {product.platform_id}: {e}")
    """

    def __init__(self, task_id: str, checkpoint_dir: str = "data/checkpoints"):
        """
        参数:
            task_id: 任务唯一标识（如 "crawl_jd_20260709"）
            checkpoint_dir: checkpoint 文件存储目录
        """
        self.task_id = task_id
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_file = self.checkpoint_dir / f"{task_id}.json"
        self._done_ids: Set[str] = set()
        self._load()

    def is_done(self, platform_id: str) -> bool:
        """检查某个 platform_id 是否已处理。"""
        return platform_id in self._done_ids

    def mark_done(self, platform_id: str):
        """标记为已处理并立即写入文件。"""
        self._done_ids.add(platform_id)
        self._save()

    def skip_done(self, items: list, key_fn: Callable = lambda x: x) -> list:
        """过滤列表，返回未处理的项。

        参数:
            items: 待处理列表
            key_fn: 从 item 提取 platform_id 的函数

        返回:
            未处理的项列表
        """
        pending = [item for item in items if not self.is_done(key_fn(item))]
        skipped = len(items) - len(pending)
        if skipped > 0:
            logger.info(f"断点续爬: 跳过 {skipped} 个已处理，剩余 {len(pending)} 个")
        return pending

    def reset(self):
        """重置检查点（删除已处理记录）。"""
        self._done_ids.clear()
        if self.checkpoint_file.exists():
            self.checkpoint_file.unlink()
            logger.info(f"检查点已重置: {self.task_id}")

    @property
    def done_count(self) -> int:
        return len(self._done_ids)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _load(self):
        """从文件加载检查点。"""
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, "r") as f:
                    data = json.load(f)
                self._done_ids = set(data.get("done_ids", []))
                updated_at = data.get("updated_at", "unknown")
                logger.info(
                    f"加载检查点 {self.task_id}: "
                    f"已处理 {len(self._done_ids)} 个 (更新于 {updated_at})"
                )
            except (json.JSONDecodeError, KeyError):
                logger.warning(f"检查点文件损坏，将重新开始: {self.checkpoint_file}")
                self._done_ids = set()
        else:
            logger.debug(f"检查点文件不存在，从零开始: {self.task_id}")

    def _save(self):
        """保存检查点到文件。"""
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "task_id": self.task_id,
            "done_ids": sorted(self._done_ids),
            "updated_at": datetime.now().isoformat(),
        }
        with open(self.checkpoint_file, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
