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


def _extract_h5_tk_value(set_cookie: str) -> str:
    """从 Set-Cookie 头取出 _m_h5_tk 的**完整取值**（{token}_{ts}）。

    这里返回完整值而非 token 片段：服务端校验时可能用到时间戳部分，
    只取 token 会改变原本的会话语义。签名时才用 ``_split_token()`` 截取。

    正则停在第一个 ``;`` 或 ``,`` 之前，因此不受
    ``Expires=Wed, 09 Jun 2021 ...`` 这类含逗号属性的干扰。
    """
    if not set_cookie:
        return ""
    m = re.search(r"_m_h5_tk=([^;,]+)", set_cookie)
    return m.group(1).strip() if m else ""


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
        """把响应 Set-Cookie 里的 _m_h5_tk 写回会话，供下一次签名使用。

        注意：不能直接按 ``,`` 切分整串 —— Set-Cookie 的属性值里本身就含逗号
        （如 ``Expires=Wed, 09 Jun 2021 10:18:14 GMT``）。
        这里用 ``http.cookies`` 的标准解析：它只把「逗号 + 空格 + name=」视为
        多条 Cookie 的分界，能正确处理 Expires 中的逗号。
        """
        for k, v in headers.items():
            if k.lower() != "set-cookie":
                continue
            # 交给专用解析器：Set-Cookie 属性值里含逗号（Expires=Wed, 09 Jun ...），
            # 手动 split(",") 会把 token 截断
            value = _extract_h5_tk_value(v)
            if value:
                self.session.cookies.set("_m_h5_tk", value, domain=".taobao.com")
                return

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
    """从搜索响应中解析商品条目（data.itemsArray 为实际数据）。

    返回每个商品的归一化字典，字段与 crawler/taobao_spider.py 的 ProductBrief 对齐：
    title / price / sales / id / shop / url / main_image_url / comment_count。
    """
    data = j.get("data", {}) or {}
    arr = data.get("itemsArray", []) or []
    items = []
    for it in arr[:limit]:
        psi = it.get("priceShowWithIcon") or {}
        price = str(psi.get("price") or it.get("price") or "")
        si = it.get("shopInfo") or {}
        shop = (si.get("title") or si.get("nick") or "") if isinstance(si, dict) else ""
        item_id = str(it.get("item_id", ""))
        # 主图：与 TaobaoSpider 一致，依次尝试多个候选键
        img = (
            it.get("pic_path")
            or it.get("img")
            or it.get("image")
            or it.get("pic_url")
            or ""
        )
        # 评价数：搜索接口未必返回，依次尝试多个候选键，缺失则为空串
        comment = str(
            it.get("commentCount")
            or it.get("totalEval")
            or it.get("comment_count")
            or it.get("feedbackNum")
            or ""
        )
        items.append({
            "title": it.get("title", ""),
            "price": price,
            "sales": str(it.get("realSales", "")),
            "id": item_id,
            "shop": shop,
            "url": f"https://item.taobao.com/item.htm?id={item_id}",
            "main_image_url": str(img),
            "comment_count": comment,
        })
    return items
