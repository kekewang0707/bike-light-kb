"""抓取合规工具 — robots.txt 遵从 + 全局限速。

为什么要这个模块
----------------
本项目的爬虫访问的是第三方电商站点。无论技术上能否绕过风控，
负责任的做法是：

1. **遵守 robots.txt**（RFC 9309）——站点明确声明 Disallow 的路径不去碰；
2. **限速**——把请求速率压到"人肉浏览"量级，避免对目标站点造成压力；
3. **可关闭但默认开启**——``BKL_CRAWLER_RESPECT_ROBOTS=true``、
   ``BKL_CRAWLER_RATE_PER_MINUTE=20``。关闭前请确认你有合法授权。

Usage::

    from crawler.compliance import RateLimiter, RobotsChecker

    limiter = RateLimiter(rate_per_minute=20)
    robots = RobotsChecker(user_agent="*", respect=True)

    if await robots.allowed(url):
        await limiter.acquire()
        ...  # 发起请求
"""

import asyncio
import time
from typing import Dict, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from loguru import logger


class RateLimiter:
    """令牌桶限速器（async + sync 双接口）。

    采用令牌桶而非简单 sleep：允许短时间内的少量突发（burst），
    长期平均速率仍被限制在 ``rate_per_minute`` 以内。

    参数:
        rate_per_minute: 长期平均速率上限（请求/分钟）
        burst:           允许的瞬时突发请求数（默认 1，即严格匀速）
    """

    def __init__(self, rate_per_minute: float = 20.0, burst: int = 1):
        self.set_rate(rate_per_minute)
        self._burst = max(1, int(burst))
        self._tokens = float(self._burst)
        self._updated_at = time.monotonic()
        # asyncio.Lock 惰性创建：避免在没有事件循环的线程/构造期绑定 loop
        self._lock: Optional[asyncio.Lock] = None

    def set_rate(self, rate_per_minute: float) -> None:
        """更新速率上限（0 或负数表示不限速）。"""
        rate = float(rate_per_minute or 0)
        self._rate = rate
        self._interval = 60.0 / rate if rate > 0 else 0.0

    def _refill(self) -> None:
        """按经过时间补充令牌，上限为 burst。"""
        now = time.monotonic()
        if self._interval > 0:
            elapsed = now - self._updated_at
            self._tokens = min(self._burst, self._tokens + elapsed / self._interval)
        else:
            self._tokens = float(self._burst)   # 不限速
        self._updated_at = now

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def acquire(self) -> None:
        """获取一个请求配额，必要时阻塞等待。"""
        if self._interval <= 0:
            return
        async with self._get_lock():
            self._refill()
            if self._tokens < 1.0:
                wait_seconds = (1.0 - self._tokens) * self._interval
                await asyncio.sleep(wait_seconds)
                self._refill()
            self._tokens -= 1.0

    def throttle(self) -> None:
        """同步版本，供非 async 代码路径使用。"""
        if self._interval <= 0:
            return
        self._refill()
        if self._tokens < 1.0:
            time.sleep((1.0 - self._tokens) * self._interval)
            self._refill()
        self._tokens -= 1.0


class RobotsChecker:
    """robots.txt 检查器 — 按域名缓存，默认遵从。

    判定规则（RFC 9309）:
    - ``respect=False`` → 一律放行（需自行确保有授权）
    - robots.txt 返回 4xx（含 404）→ 视为无限制，放行
    - robots.txt 返回 5xx → 视为"站点要求暂停访问"，**拒绝**
    - 网络异常/超时 → 无法确认站点意图，放行但记录 warning
    - 若站点声明了 ``Crawl-delay`` → 通过 ``crawl_delay()`` 暴露给调用方调速
    """

    def __init__(
        self,
        user_agent: str = "*",
        respect: bool = True,
        timeout: float = 10.0,
        session=None,
    ):
        self.user_agent = user_agent
        self.respect = respect
        self.timeout = timeout
        self._session = session          # 可注入 httpx.AsyncClient（便于测试）
        self._cache: Dict[str, Optional[RobotFileParser]] = {}

    @staticmethod
    def _robots_url(url: str) -> str:
        parts = urlparse(url)
        return f"{parts.scheme}://{parts.netloc}/robots.txt"

    async def allowed(self, url: str) -> bool:
        """判断给定 URL 是否允许抓取。"""
        if not self.respect:
            return True

        robots_url = self._robots_url(url)
        domain = urlparse(url).netloc

        if domain not in self._cache:
            self._cache[domain] = await self._load(robots_url)

        parser = self._cache[domain]
        if parser is None:
            # 加载失败（网络异常/超时）：放行但告警，由运维决定是否介入
            return True

        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception as e:  # noqa: BLE001 - robots 解析异常不应中断爬取
            logger.warning(f"robots.txt 解析异常 [{domain}]: {e}，按放行处理")
            return True

    async def crawl_delay(self, url: str) -> Optional[float]:
        """返回站点声明的 Crawl-delay（秒），未声明返回 None。"""
        domain = urlparse(url).netloc
        if domain not in self._cache:
            self._cache[domain] = await self._load(self._robots_url(url))
        parser = self._cache[domain]
        if parser is None:
            return None
        try:
            delay = parser.crawl_delay(self.user_agent)
            return float(delay) if delay is not None else None
        except Exception:  # noqa: BLE001
            return None

    async def _load(self, robots_url: str) -> Optional[RobotFileParser]:
        """拉取并解析 robots.txt。

        返回:
            RobotFileParser 实例；5xx 或解析失败返回 None。
            5xx 时调用方应拒绝访问，因此额外用 ``_server_error`` 标记。
        """
        import httpx   # 局部导入：合规模块被同步代码引用时不必强依赖 httpx

        try:
            if self._session is not None:
                resp = await self._session.get(robots_url)
                status, text = resp.status_code, resp.text
            else:
                async with httpx.AsyncClient(
                    timeout=self.timeout, follow_redirects=True
                ) as client:
                    resp = await client.get(robots_url)
                    status, text = resp.status_code, resp.text
        except Exception as e:  # noqa: BLE001 - 网络异常：放行 + 告警
            logger.warning(f"robots.txt 拉取失败 [{robots_url}]: {e}，按放行处理")
            return None

        if 500 <= status < 600:
            logger.error(
                f"robots.txt 返回 {status}（站点异常）[{robots_url}]，"
                f"按 RFC 9309 暂停抓取该域名"
            )
            self._server_error = True
            return _DenyAllParser()

        if 400 <= status < 500:
            logger.info(f"robots.txt 不存在（{status}）[{robots_url}]，视为无限制")
            parser = RobotFileParser()
            parser.parse("")       # 空规则 = 全部允许
            return parser

        parser = RobotFileParser()
        try:
            parser.parse(text.splitlines())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"robots.txt 内容异常 [{robots_url}]: {e}，按放行处理")
            return None
        return parser


class _DenyAllParser:
    """robots.txt 5xx 时的兜底策略：拒绝一切。"""

    def can_fetch(self, user_agent: str, url: str) -> bool:
        return False

    def crawl_delay(self, user_agent: str):
        return None
