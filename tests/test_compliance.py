"""合规模块测试 — 限速器与 robots.txt 遵从。

对应审计 P2#11：爬虫必须限速、遵从 robots.txt。
robots 部分用注入的假 session，不触网。
"""

import asyncio
import time

import pytest

from crawler.compliance import RateLimiter, RobotsChecker


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeSession:
    """记录调用次数的最小 httpx.AsyncClient 替身。"""

    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text
        self.calls = []

    async def get(self, url: str):
        self.calls.append(url)
        return _FakeResponse(self.status_code, self.text)


# ---------------------------------------------------------------------------
# 限速器
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_limiter_no_wait_when_rate_disabled():
    limiter = RateLimiter(rate_per_minute=0)
    start = time.monotonic()
    for _ in range(5):
        await limiter.acquire()
    assert time.monotonic() - start < 0.05


@pytest.mark.asyncio
async def test_limiter_first_call_is_immediate():
    """首个请求不应被阻塞（令牌桶初始为满）。"""
    limiter = RateLimiter(rate_per_minute=60, burst=1)   # 1 次/秒
    start = time.monotonic()
    await limiter.acquire()
    assert time.monotonic() - start < 0.05


@pytest.mark.asyncio
async def test_limiter_throttles_burst():
    """burst=1 时，连续 3 次请求应至少等待 2 个间隔。"""
    limiter = RateLimiter(rate_per_minute=1200, burst=1)  # 20 次/秒 → 间隔 0.05s
    start = time.monotonic()
    for _ in range(3):
        await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.08, f"未生效限速，仅耗时 {elapsed:.3f}s"
    assert elapsed < 0.5, f"限速过猛，耗时 {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_limiter_burst_allows_short_spike():
    limiter = RateLimiter(rate_per_minute=60, burst=3)   # 1 次/秒，允许 3 连发
    start = time.monotonic()
    for _ in range(3):
        await limiter.acquire()
    assert time.monotonic() - start < 0.05


def test_limiter_sync_throttle():
    limiter = RateLimiter(rate_per_minute=1200, burst=1)
    start = time.monotonic()
    for _ in range(3):
        limiter.throttle()
    assert time.monotonic() - start >= 0.08


def test_limiter_set_rate_updates_interval():
    limiter = RateLimiter(rate_per_minute=60)
    assert abs(limiter._interval - 1.0) < 1e-9
    limiter.set_rate(600)
    assert abs(limiter._interval - 0.1) < 1e-9


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_robots_respect_false_skips_fetch():
    session = _FakeSession(200, "User-agent: *\nDisallow: /")
    checker = RobotsChecker(respect=False, session=session)
    assert await checker.allowed("https://example.com/anything") is True
    assert session.calls == []       # 未联网


@pytest.mark.asyncio
async def test_robots_denies_disallowed_path():
    session = _FakeSession(200, "User-agent: *\nDisallow: /search")
    checker = RobotsChecker(session=session)
    assert await checker.allowed("https://example.com/search?q=1") is False
    assert await checker.allowed("https://example.com/item/1") is True


@pytest.mark.asyncio
async def test_robots_allows_all_when_404():
    """robots.txt 不存在（4xx）视为无限制。"""
    checker = RobotsChecker(session=_FakeSession(404))
    assert await checker.allowed("https://example.com/any") is True


@pytest.mark.asyncio
async def test_robots_denies_on_server_error():
    """5xx 表示站点异常，应暂停抓取（RFC 9309）。"""
    checker = RobotsChecker(session=_FakeSession(503))
    assert await checker.allowed("https://example.com/any") is False


@pytest.mark.asyncio
async def test_robots_allows_on_network_error_but_warns():
    class _BoomSession:
        async def get(self, url):
            raise TimeoutError("connect timeout")

    checker = RobotsChecker(session=_BoomSession())
    assert await checker.allowed("https://example.com/any") is True


@pytest.mark.asyncio
async def test_robots_caches_per_domain():
    session = _FakeSession(200, "User-agent: *\nDisallow: /private")
    checker = RobotsChecker(session=session)
    await checker.allowed("https://example.com/a")
    await checker.allowed("https://example.com/b")
    await checker.allowed("https://example.com/c")
    assert len(session.calls) == 1      # 同一域名只拉取一次

    await checker.allowed("https://other.com/a")
    assert len(session.calls) == 2      # 换域名才重新拉取


@pytest.mark.asyncio
async def test_robots_crawl_delay_exposed():
    session = _FakeSession(200, "User-agent: *\nCrawl-delay: 5\nDisallow:")
    checker = RobotsChecker(session=session)
    assert await checker.crawl_delay("https://example.com/") == 5.0


@pytest.mark.asyncio
async def test_robots_robots_url_built_from_origin():
    assert RobotsChecker._robots_url(
        "https://main.m.taobao.com/search?q=%E7%81%AF&page=2"
    ) == "https://main.m.taobao.com/robots.txt"


@pytest.mark.asyncio
async def test_robots_respects_named_user_agent():
    body = "User-agent: badbot\nDisallow: /\n\nUser-agent: *\nDisallow: /secret"
    assert await RobotsChecker(user_agent="badbot", session=_FakeSession(200, body)).allowed(
        "https://example.com/secret"
    ) is False
    assert await RobotsChecker(user_agent="goodbot", session=_FakeSession(200, body)).allowed(
        "https://example.com/secret"
    ) is False
    assert await RobotsChecker(user_agent="goodbot", session=_FakeSession(200, body)).allowed(
        "https://example.com/public"
    ) is True
