"""异步图片下载器。

将远程图片下载到本地，按 {product_id}/{image_type}_{n}.jpg 组织目录结构。
图片下载失败不阻塞 — 返回 None，记录日志供事后补下。

特性:
- 异步并发下载（httpx + asyncio.Semaphore）
- 自动创建目录
- 下载失败不阻塞其他图片
- 生成下载报告（成功数/失败数）
- 下载完成后可将图片记录同步写入 product_images 表

Usage::

    from crawler.downloader import ImageDownloader
    from crawler.schemas import ImageTask

    downloader = ImageDownloader(concurrency=5, save_dir="data/images")
    tasks = [
        ImageTask(url="https://...", product_id=1, image_type="main", local_path="1/main_1.jpg"),
        ...
    ]
    report = await downloader.download_batch(tasks)
    # 将下载成功的图片信息写入数据库
    synced = downloader.sync_product_images(tasks, report["paths"])
    print(f"成功: {report['success']}, 失败: {report['failed']}, 入库: {synced}")
"""

import asyncio
import ipaddress
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin, urlsplit

import httpx
from loguru import logger

from crawler.schemas import ImageTask
from models import get_session, ProductImage, CRUDBase


class ImageDownloader:
    """异步图片下载器 — 信号量控制并发。

    参数:
        concurrency: 最大并发下载数（默认 5）
        save_dir:    图片根目录（默认 data/images/）
        timeout:     单张图片下载超时秒数（默认 30）
    """

    # 允许的图片 Content-Type
    ALLOWED_CONTENT_TYPES = {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/bmp",
        "image/svg+xml",
    }

    # ---- SSRF 防护 ----
    # 图片 URL 来自网络响应，必须限制在受信任的淘宝 CDN 域名，并禁止指向内网地址。
    ALLOWED_IMAGE_DOMAIN_SUFFIXES = (
        ".alicdn.com",
        ".taobao.com",
        ".taobaocdn.com",
        ".tbcdn.cn",
        ".taobao.net",
    )

    @staticmethod
    def _is_safe_image_url(
        url: str,
        allowed_suffixes: tuple = ALLOWED_IMAGE_DOMAIN_SUFFIXES,
    ) -> bool:
        """校验图片 URL 是否可安全下载（防 SSRF）。

        - 仅允许 http/https 协议
        - 拒绝指向内网/保留地址的 IP 字面量（127/10/172.16/192.168/169.254/::1 等）
        - 主机名必须匹配受信任的淘宝 CDN 域名后缀（防 DNS 重绑定到内网）
        """
        if not url or not isinstance(url, str):
            return False
        try:
            parsed = urlsplit(url.strip())
        except ValueError:
            return False
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").strip().lower()
        if not host:
            return False
        # IP 字面量：禁止任何非公网地址（含 169.254.169.254 元数据端点）
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                return False
        except ValueError:
            pass  # 主机名，继续走域名白名单
        if allowed_suffixes:
            if not any(host == s or host.endswith(s) for s in allowed_suffixes):
                return False
        return True

    def __init__(
        self,
        concurrency: int = 5,
        save_dir: str = "data/images",
        timeout: int = 30,
    ):
        self.concurrency = concurrency
        self.save_dir = Path(save_dir)
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    async def download_batch(self, tasks: List[ImageTask]) -> dict:
        """批量下载图片并返回统计报告。

        参数:
            tasks: ImageTask 列表

        返回:
            {"success": 成功数, "failed": 失败数, "paths": 成功路径列表}
        """
        if not tasks:
            return {"success": 0, "failed": 0, "paths": []}

        logger.info(f"开始下载 {len(tasks)} 张图片 (并发={self.concurrency})")

        results = await asyncio.gather(
            *[self._download_one(task) for task in tasks],
            return_exceptions=True,
        )

        success_paths = []
        failed_count = 0

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"图片下载异常 #{i}: {result}")
                failed_count += 1
            elif result is not None:
                success_paths.append(result)
            else:
                failed_count += 1

        report = {
            "success": len(success_paths),
            "failed": failed_count,
            "paths": success_paths,
        }

        logger.info(
            f"图片下载完成: 成功 {report['success']}, "
            f"失败 {report['failed']} "
            f"({report['success'] / max(len(tasks), 1) * 100:.1f}%)"
        )
        return report

    def sync_product_images(
        self,
        tasks: List[ImageTask],
        success_paths: List[str],
    ) -> int:
        """下载完成后，将成功下载的图片同步写入 product_images 表。

        按 (product_id, image_url) 去重 — 已存在则更新 local_path，
        不存在则创建新记录。保证重复采集不会产生重复图片记录。

        参数:
            tasks:         本次下载的全部 ImageTask 列表
            success_paths: download_batch() 返回的成功路径列表

        返回:
            成功写入/更新的记录数
        """
        if not tasks or not success_paths:
            return 0

        image_crud = CRUDBase(ProductImage)
        success_set = set(success_paths)
        synced = 0

        session = next(get_session())
        try:
            for task in tasks:
                # 只处理下载成功的
                if task.local_path not in success_set:
                    continue
                # 跳过没有 URL 的（不太可能，但做防御）
                if not task.url:
                    continue

                try:
                    image_crud.upsert_by(
                        session,
                        filters={
                            "product_id": task.product_id,
                            "image_url": task.url,
                        },
                        updates={
                            "image_type": task.image_type,
                            "local_path": task.local_path,
                            "sort_order": task.sort_order,
                        },
                    )
                    synced += 1
                except Exception as e:
                    logger.error(
                        f"图片记录写入失败 "
                        f"[product_id={task.product_id}, url={task.url[:80]}...]: {e}"
                    )
        except Exception as e:
            session.rollback()
            logger.error(f"sync_product_images 异常: {e}")
            raise
        finally:
            session.close()

        if synced > 0:
            logger.info(f"图片记录已入库: {synced} 条")
        return synced

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """懒加载 HTTP 客户端。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=False,  # 手动处理重定向以便逐跳校验 URL（防 SSRF）
                limits=httpx.Limits(max_connections=self.concurrency * 2),
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/122.0.0.0 Safari/537.36",
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Referer": "https://www.taobao.com/",
                },
            )
        return self._client

    async def _download_one(self, task: ImageTask) -> Optional[str]:
        """下载单张图片（带并发控制）。"""
        async with self._semaphore:
            return await self._do_download(task)

    async def _do_download(self, task: ImageTask) -> Optional[str]:
        """执行实际的下载操作（带并发控制与 SSRF 防护）。"""
        # 检查本地是否已存在
        full_path = self.save_dir / task.local_path
        if full_path.exists() and full_path.stat().st_size > 0:
            logger.debug(f"图片已存在，跳过: {task.local_path}")
            return task.local_path

        # SSRF 防护：URL 不在白名单或指向内网时直接拒绝，不发起请求
        if not self._is_safe_image_url(task.url):
            logger.warning(
                f"拒绝下载（URL 不在白名单或指向内网）: {task.url[:80]}..."
            )
            return None

        # 创建目录
        full_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            client = await self._get_client()
            content, content_type = await self._fetch_url(client, task.url)
            if content is None:
                return None

            # 检查 Content-Type
            if content_type:
                ct = content_type.lower()
                if not any(c in ct for c in self.ALLOWED_CONTENT_TYPES):
                    logger.debug(f"非图片 Content-Type ({ct}): {task.url[:80]}...")
                    return None

            if not content:
                logger.debug(f"图片内容为空: {task.url[:80]}...")
                return None

            full_path.write_bytes(content)
            return task.local_path

        except httpx.TimeoutException:
            logger.debug(f"图片下载超时 ({self.timeout}s): {task.url[:80]}...")
            return None
        except Exception as e:
            logger.debug(f"图片下载异常: {e}, url={task.url[:80]}...")
            return None

    async def _fetch_url(
        self,
        client: httpx.AsyncClient,
        url: str,
        depth: int = 0,
    ):
        """带重定向逐跳校验的下载辅助方法（防 SSRF）。

        每一步重定向都重新走 _is_safe_image_url 校验，确保最终也不会被
        引导到内网/非白名单地址。最多跟随 5 跳。
        返回 (content, content_type) 或 (None, None)。
        """
        if depth > 5:
            return None, None
        if not self._is_safe_image_url(url):
            return None, None
        try:
            resp = await client.get(url)
        except Exception as e:
            logger.debug(f"图片请求异常: {e}, url={url[:80]}...")
            return None, None

        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location")
            if not loc:
                return None, None
            next_url = urljoin(url, loc)
            return await self._fetch_url(client, next_url, depth + 1)

        if resp.status_code != 200:
            logger.debug(f"图片下载失败 (HTTP {resp.status_code}): {url[:80]}...")
            return None, None

        return resp.content, resp.headers.get("content-type", "")

    @staticmethod
    def _build_local_path(product_id: int, image_type: str, sort_order: int) -> str:
        """构建本地存储路径。

        格式: {product_id}/{image_type}_{sort_order}.jpg
        示例: 1/main_1.jpg
        """
        return f"{product_id}/{image_type}_{sort_order}.jpg"
