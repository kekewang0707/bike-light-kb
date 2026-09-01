"""淘宝 mtop 接口签名请求客户端（逆向用，curl_cffi 伪装 Chrome TLS）。

参考 cn-scraper-mcp 的成熟实现（https://github.com/goesByhc/cn-scraper-mcp）：
淘宝 mtop 网关位于 https://h5api.m.taobao.com/h5/{api}/{version}/，要求：
- appKey（H5 常用 12574478）
- 每会话的 `_m_h5_tk` token（值形如 `{token}_{ts}`，网关首次请求以 Set-Cookie 下发）
- 请求签名：sign = md5(f"{token}&{t}&{appKey}&{data}")
- 登录 Cookie（cookie2 / _tb_token_ / t / unb 等）
- **用 curl_cffi 伪装 Chrome 的 TLS 指纹**，这是绕过阿里反爬的关键；httpx 做不到。

用法::
    import asyncio
    from crawler.mtop import MtopClient

    client = MtopClient()
    j = await client.call("mtop.taobao.wsearch.appsearch", "1.0", {
        "q": "自行车灯", "search_action": "initiative", "page": "1", "n": "24",
        "sversion": "9.9.9",
    })
    print(j.get("ret"), len(j.get("data", {}).get("itemsArray", [])))

注意: 若 `_m_h5_tk` 不在 Cookie 中，首次调用会以空 token 触发网关下发（Set-Cookie），
     并在 TOKEN 错误时自动重试。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from crawler.taobao_auth import CookieManager, load_cookies

try:
    from curl_cffi import requests as creq
    HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover
    HAS_CURL_CFFI = False

MTOP_BASE = "https://h5api.m.taobao.com/h5/"
DEFAULT_APP_KEY = "12574478"  # 淘宝 H5 常用 appKey
JSV = "2.7.2"
UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"
)


# ---------------------------------------------------------------------------
# 签名 / 工具
# ---------------------------------------------------------------------------

def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def mtop_sign(token: str, t: str, app_key: str, data_str: str) -> str:
    """mtop 签名：md5(token & t & appKey & data)。"""
    return _md5(f"{token}&{t}&{app_key}&{data_str}")


def _compact_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _split_token(cookie_value: str) -> str:
    """从 _m_h5_tk 值中提取 token 部分（{token}_{ts} 或 {token},{ts}）。"""
    if not cookie_value:
        return ""
    return cookie_value.split("_", 1)[0].split(",", 1)[0].strip()


def _parse_token_from_set_cookie(set_cookie: str) -> str:
    """从 Set-Cookie 头解析 _m_h5_tk 的 token 部分。"""
    if not set_cookie:
        return ""
    m = re.search(r"_m_h5_tk=([^;,]+)", set_cookie)
    return _split_token(m.group(1)) if m else ""


# ---------------------------------------------------------------------------
# mtop 客户端
# ---------------------------------------------------------------------------

class MtopClient:
    """带登录 Cookie 的 mtop 签名请求客户端（curl_cffi 伪装 Chrome）。

    自动处理 _m_h5_tk 的获取（首次请求网关 Set-Cookie 下发）与 TOKEN 错误重试。
    """

    def __init__(
        self,
        app_key: str = DEFAULT_APP_KEY,
        cookie_mgr: Optional[CookieManager] = None,
        tries: int = 4,
    ):
        if not HAS_CURL_CFFI:
            raise RuntimeError("需要 curl-cffi：pip install curl-cffi")

        self.app_key = app_key
        self.cookie_mgr = cookie_mgr or CookieManager(platform="taobao")
        self.tries = tries

        cookies = load_cookies(self.cookie_mgr) or []
        if not cookies:
            raise RuntimeError("未找到有效的淘宝 Cookie，请先运行 login_and_save() 完成扫码登录。")

        # curl_cffi 会话：伪装 Chrome TLS 指纹
        self.session = creq.Session(impersonate="chrome")
        for c in cookies:
            domain = c.get("domain") or ".taobao.com"
            self.session.cookies.set(
                c.get("name"), c.get("value", ""), domain=domain
            )

    # ------------------------------------------------------------------
    # token / 请求
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        value = self.session.cookies.get("_m_h5_tk") or ""
        return _split_token(value)

    def _refresh_from_headers(self, headers) -> None:
        """把响应 Set-Cookie 里的 _m_h5_tk 写回会话，供下一次签名使用。"""
        set_cookie = ""
        for k, v in headers.items():
            if k.lower() == "set-cookie":
                set_cookie = (set_cookie + "," + v) if set_cookie else v
        if set_cookie:
            for part in set_cookie.split(","):
                part = part.strip()
                if part.startswith("_m_h5_tk="):
                    value = part.split("=", 1)[1].split(";")[0].strip()
                    self.session.cookies.set("_m_h5_tk", value, domain=".taobao.com")
                    break

    # ------------------------------------------------------------------
    # 调用
    # ------------------------------------------------------------------

    async def call(
        self,
        api: str,
        version: str,
        data: Dict[str, Any] = None,
        tries: Optional[int] = None,
    ) -> Tuple[Dict[str, Any], Any]:
        """调用一个 mtop 接口（GET），带签名与 TOKEN 错误重试。

        curl_cffi 是同步库，这里通过 asyncio.to_thread 在事件循环内跑，对外仍是 async。

        返回:
            (解析后的 JSON, curl_cffi Response)
        """
        data = data or {}
        return await asyncio.to_thread(self._call_sync, api, version, data, tries)

    def _call_sync(
        self, api: str, version: str, data: Dict[str, Any], tries: Optional[int]
    ) -> Tuple[Dict[str, Any], Any]:
        data_str = _compact_json(data)
        tries = tries or self.tries
        last: Optional[Dict[str, Any]] = None

        for attempt in range(tries):
            token = self._get_token()
            t = str(int(time.time() * 1000))
            sign = mtop_sign(token, t, self.app_key, data_str)

            params = {
                "jsv": JSV,
                "appKey": self.app_key,
                "t": t,
                "sign": sign,
                "api": api,
                "v": version,
                "type": "originaljson",
                "dataType": "json",
                "H5Request": "true",
                "data": data_str,
            }
            url = f"{MTOP_BASE}{api.lower()}/{version}/"

            headers = {
                "Referer": "https://h5.m.taobao.com/",
                "Origin": "https://h5.m.taobao.com",
                "Accept": "application/json",
            }

            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=15)
                j = resp.json()
            except Exception as e:
                logger.warning(f"mtop 请求异常 [第{attempt + 1}次]: {e}")
                last = {"error": str(e)}
                continue

            # 更新会话 token（网关可通过 Set-Cookie 轮换）
            self._refresh_from_headers(resp.headers)
            last = j

            ret = j.get("ret", [])
            ret_str = "::".join(str(r) for r in ret) if isinstance(ret, list) else str(ret)

            # TOKEN 错误 → 用刷新后的 token 重试
            if "TOKEN" in ret_str and attempt < tries - 1:
                logger.info("token 已刷新，重试...")
                continue

            return j, resp

        return last or {"error": "All retries exhausted"}, None


# ---------------------------------------------------------------------------
# 便捷：搜索
# ---------------------------------------------------------------------------

async def search(
    keyword: str,
    page: int = 1,
    *,
    n: int = 24,
    client: Optional[MtopClient] = None,
) -> Dict[str, Any]:
    """调用淘宝搜索 mtop 接口（mtop.taobao.wsearch.appsearch v1.0）。

    返回:
        接口返回的 JSON；商品列表通常在 data["itemsArray"]，见 parse_items()。
    """
    client = client or MtopClient()
    data = {
        "q": keyword,
        "search_action": "initiative",
        "page": str(page),
        "n": str(n),
        "sversion": "9.9.9",
    }
    j, _ = await client.call("mtop.taobao.wsearch.appsearch", "1.0", data=data)
    return j


def parse_items(j: Dict[str, Any], limit: int = 20) -> List[Dict[str, Any]]:
    """从搜索响应中解析商品条目（data.itemsArray 为实际数据）。"""
    data = j.get("data", {}) or {}
    arr = data.get("itemsArray", []) or []
    items = []
    for it in arr[:limit]:
        psi = it.get("priceShowWithIcon") or {}
        price = str(psi.get("price") or it.get("price") or "")
        si = it.get("shopInfo") or {}
        shop = (si.get("title") or si.get("nick") or "") if isinstance(si, dict) else ""
        item_id = str(it.get("item_id", ""))
        items.append({
            "title": it.get("title", ""),
            "price": price,
            "sales": str(it.get("realSales", "")),
            "id": item_id,
            "shop": shop,
            "url": f"https://item.taobao.com/item.htm?id={item_id}",
        })
    return items
