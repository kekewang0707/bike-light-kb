"""Cookie/Session 持久化管理。

京东登录态 Cookie 有效期约 30 天。通过持久化 Cookie：
- 避免每次启动爬虫都要重新登录
- 登录后的数据更完整（会员价、优惠信息等）
- 使用真实登录态可降低被风控的概率

Usage::

    from crawler.cookie_manager import CookieManager

    mgr = CookieManager()
    cookies = mgr.load()

    if cookies and not mgr.is_expired():
        await context.add_cookies(cookies)
    else:
        # 需要登录
        ...
        cookies = await context.cookies()
        mgr.save(cookies)
"""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional

from loguru import logger


class CookieManager:
    """Cookie 持久化管理器。"""

    def __init__(
        self,
        platform: str = "jd",
        cookie_dir: str = "data/cookies",
        expiry_days: int = 30,
        warn_days: int = 3,
    ):
        """
        参数:
            platform: 平台标识 (jd/taobao/pdd)
            cookie_dir: Cookie 文件存储目录
            expiry_days: Cookie 有效期（天）
            warn_days: 提前多少天警告过期
        """
        self.platform = platform
        self.cookie_dir = Path(cookie_dir)
        self.cookie_file = self.cookie_dir / f"{platform}_cookies.json"
        self.expiry_days = expiry_days
        self.warn_days = warn_days

    # ------------------------------------------------------------------
    # 保存 / 加载
    # ------------------------------------------------------------------

    def save(self, cookies: List[dict]):
        """将 Playwright cookies 序列化到文件。

        参数:
            cookies: Playwright context.cookies() 返回的 Cookie 列表
        """
        self.cookie_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "platform": self.platform,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "expiry_days": self.expiry_days,
            "cookies": cookies,
        }

        with open(self.cookie_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        logger.info(f"已保存 {len(cookies)} 个 {self.platform} Cookie → {self.cookie_file}")

    def load(self) -> Optional[List[dict]]:
        """加载已保存的 Cookie。

        返回:
            Cookie 列表，如果文件不存在或格式错误返回 None
        """
        if not self.cookie_file.exists():
            logger.debug(f"Cookie 文件不存在: {self.cookie_file}")
            return None

        try:
            with open(self.cookie_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            cookies = data.get("cookies", [])
            saved_at = data.get("saved_at", "unknown")

            age_days = self._age_days(data.get("saved_at"))
            logger.info(
                f"已加载 {len(cookies)} 个 {self.platform} Cookie "
                f"(保存于 {saved_at}，距今 {age_days:.1f} 天)"
            )

            if self.is_expired(data):
                logger.warning(f"{self.platform} Cookie 已过期（{age_days:.1f} 天前保存）")
                return None

            return cookies

        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Cookie 文件解析失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 过期检测
    # ------------------------------------------------------------------

    def is_expired(self, data: dict = None) -> bool:
        """检查 Cookie 是否已过期。

        参数:
            data: load() 返回的数据，为 None 时从文件读取

        返回:
            True 表示已过期或即将过期
        """
        if data is None:
            if not self.cookie_file.exists():
                return True
            try:
                with open(self.cookie_file, "r") as f:
                    data = json.load(f)
            except Exception:
                return True

        age_days = self._age_days(data.get("saved_at"))
        return age_days > (self.expiry_days - self.warn_days)

    def days_until_expiry(self) -> Optional[float]:
        """距离过期还有多少天。

        返回:
            剩余天数，-1 表示已过期，None 表示文件不存在
        """
        if not self.cookie_file.exists():
            return None
        try:
            with open(self.cookie_file, "r") as f:
                data = json.load(f)
            age_days = self._age_days(data.get("saved_at"))
            return self.expiry_days - age_days
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _age_days(self, saved_at_str: str) -> float:
        """计算 Cookie 从保存到现在的天数。"""
        try:
            saved_at = datetime.fromisoformat(saved_at_str)
            now = datetime.now(timezone.utc)
            return (now - saved_at).total_seconds() / 86400
        except (ValueError, TypeError):
            return float("inf")  # 无法解析则视为过期
