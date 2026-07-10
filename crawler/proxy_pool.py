"""代理 IP 池管理。

京东严重依赖 IP 风控，数据中心 IP 可能在几分钟内被封。
必须使用中国住宅 IP 代理。

初期方案（手动配置）:
  在 .env 中配置 3-5 个固定住宅代理 IP，被限流时轮换。

后期方案（付费代理池，Phase 4）:
  接入快代理/亿牛云 API 自动获取 IP，按可用时长自动轮换。

环境变量配置示例::

    BKL_PROXY_LIST=ip1:port:user:pass,ip2:port:user:pass

Usage::

    from crawler.proxy_pool import ProxyPool

    pool = ProxyPool()
    proxy = pool.get_next()
    if proxy:
        context = browser.new_context(proxy=proxy)
"""

import os
import time
import threading
from typing import List, Optional, Dict

from loguru import logger


class ProxyPool:
    """代理 IP 池 — 轮换 + 封禁冷却。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._proxies: List[Dict] = self._load_from_config()
        self._blocked: Dict[int, float] = {}  # proxy_index → 解封时间戳
        self._current_idx = 0
        self._cooldown_seconds = 1800  # 封禁后冷却 30 分钟

        if self._proxies:
            logger.info(f"代理池已加载 {len(self._proxies)} 个代理")
        else:
            logger.warning("代理池为空（未配置 BKL_PROXY_LIST），将使用直连")

    # ------------------------------------------------------------------
    # 获取代理
    # ------------------------------------------------------------------

    def get_next(self) -> Optional[Dict]:
        """获取下一个可用代理。

        自动跳过冷却中的 IP。如果所有 IP 都在冷却中，返回最早解封的那个。

        返回:
            代理配置字典 {'server': 'http://ip:port', 'username': '...', 'password': '...'}
            如果代理池为空，返回 None（使用直连）
        """
        if not self._proxies:
            return None

        with self._lock:
            now = time.time()
            n = len(self._proxies)

            # 尝试找一个未被封禁的代理
            for _ in range(n):
                proxy = self._proxies[self._current_idx]
                unblock_time = self._blocked.get(self._current_idx, 0)

                self._current_idx = (self._current_idx + 1) % n

                if now >= unblock_time:
                    return proxy

            # 所有代理都在冷却中 — 返回最早解封的那个
            earliest_idx = min(self._blocked, key=self._blocked.get)
            remaining = self._blocked[earliest_idx] - now
            logger.warning(
                f"所有代理都在冷却中，使用最早解封的代理 "
                f"(还需等待 {remaining:.0f}s)"
            )
            return self._proxies[earliest_idx]

    def get_current_ip(self) -> str:
        """获取当前代理的 IP 地址（用于日志）。"""
        if not self._proxies:
            return "直连"
        proxy = self._proxies[self._current_idx]
        server = proxy.get("server", "")
        # server 格式: http://ip:port
        return server.split("://")[-1].split(":")[0] if "://" in server else server

    # ------------------------------------------------------------------
    # 封禁管理
    # ------------------------------------------------------------------

    def mark_blocked(self, proxy: Dict = None):
        """标记当前代理为被封禁（进入冷却期）。

        参数:
            proxy: 被封的代理配置。为 None 时标记当前代理。
        """
        if not self._proxies:
            return

        with self._lock:
            if proxy:
                # 查找该代理的索引
                for i, p in enumerate(self._proxies):
                    if p.get("server") == proxy.get("server"):
                        self._blocked[i] = time.time() + self._cooldown_seconds
                        logger.warning(f"IP {self._get_ip_str(p)} 进入冷却 {self._cooldown_seconds}s")
                        return
            else:
                # 标记上一个使用的代理
                idx = (self._current_idx - 1) % len(self._proxies)
                self._blocked[idx] = time.time() + self._cooldown_seconds
                logger.warning(f"IP {self._get_ip_str(self._proxies[idx])} 进入冷却 {self._cooldown_seconds}s")

    def is_blocked(self, proxy: Dict = None) -> bool:
        """检查代理是否在冷却中。"""
        if not self._proxies:
            return False
        with self._lock:
            idx = self._current_idx if proxy is None else next(
                (i for i, p in enumerate(self._proxies) if p.get("server") == proxy.get("server")),
                self._current_idx,
            )
            return time.time() < self._blocked.get(idx, 0)

    @property
    def available_count(self) -> int:
        """当前可用的代理数量。"""
        if not self._proxies:
            return 0
        now = time.time()
        return sum(1 for i in range(len(self._proxies)) if now >= self._blocked.get(i, 0))

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _load_from_config(self) -> List[Dict]:
        """从环境变量加载代理列表。

        格式: BKL_PROXY_LIST=ip:port:user:pass,ip:port:user:pass

        每个代理配置为 Playwright 兼容格式:
            {
                "server": "http://ip:port",
                "username": "user",
                "password": "pass",
            }
        """
        raw = os.getenv("BKL_PROXY_LIST", "")
        if not raw:
            return []

        proxies = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            parts = item.split(":")
            if len(parts) >= 2:
                proxy = {"server": f"http://{parts[0]}:{parts[1]}"}
                if len(parts) >= 4:
                    proxy["username"] = parts[2]
                    proxy["password"] = parts[3]
                proxies.append(proxy)

        return proxies

    @staticmethod
    def _get_ip_str(proxy: Dict) -> str:
        server = proxy.get("server", "unknown")
        return server.split("://")[-1] if "://" in server else server
