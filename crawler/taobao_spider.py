"""淘宝/天猫移动端爬虫。

使用手机版页面（iPhone 模拟），通过拦截移动端 API 获取商品数据。
移动端页面反爬更宽松，且 API 返回 JSON 格式数据。

架构:
- login_manually() — 移动端扫码登录，状态持久化
- setup() — 自动加载持久化登录态（移动端 viewport）
- search() — 拦截 h5api.m.taobao.com 搜索 API → 解析 JSON
- get_detail() — 移动端详情页
- get_reviews() — 天猫评价 API（httpx 直调，无需浏览器）
"""

import asyncio
import json
import random
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

import httpx
from loguru import logger

from crawler.anti_detect import AntiDetect
from crawler.base import BaseSpider
from crawler.cookie_manager import CookieManager
from crawler.schemas import (
    ProductBrief,
    ProductDetail,
    ReviewData,
)

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


# iPhone 移动端 UA（淘宝移动端用这个）
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Mobile/15E148 Safari/604.1"
)


class TaobaoSpider(BaseSpider):
    """淘宝/天猫移动端爬虫。

    使用 iPhone 模拟访问移动端页面，拦截 API JSON 响应解析商品。
    """

    platform = "taobao"

    PERSISTENT_PROFILE_DIR = Path("data/browser_taobao_profile")

    # 移动端 viewport（iPhone 14 Pro）
    MOBILE_VIEWPORT = {"width": 390, "height": 844}

    # 搜索 API 特征 URL
    SEARCH_API_PATTERN = "h5api.m.taobao.com"

    def __init__(
        self,
        headless: bool = False,
        cookie_manager: Optional[CookieManager] = None,
    ):
        self.headless = headless
        self.cookie_mgr = cookie_manager or CookieManager(platform="taobao")

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._httpx_client: Optional[httpx.AsyncClient] = None
        self._request_count = 0
        self._start_time = 0.0
        self._captcha_triggered = False

        # 拦截到的搜索 API 响应
        self._search_api_responses: List[dict] = []

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def setup(self):
        self._start_time = time.time()
        self._request_count = 0
        self._captcha_triggered = False
        self._search_api_responses = []

        if HAS_PLAYWRIGHT:
            self._playwright = await async_playwright().start()

            if self.PERSISTENT_PROFILE_DIR.exists():
                await self._setup_persistent_context()
            else:
                await self._setup_standalone()

        # httpx（移动端 UA）
        cookie_header = self._build_cookie_header()
        self._httpx_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
            headers={
                "User-Agent": MOBILE_UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        if cookie_header:
            self._httpx_client.headers["Cookie"] = cookie_header

    async def teardown(self):
        if self._httpx_client:
            await self._httpx_client.aclose()
        if self._page:
            try:
                cookies = await self._context.cookies()
                self.cookie_mgr.save(cookies)
            except Exception:
                pass
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------

    async def search(
        self, keyword: str, sort_by: str = "sales", max_items: int = 100
    ) -> List[ProductBrief]:
        logger.info(f"搜索: {keyword!r} (sort={sort_by}, max={max_items})")

        if HAS_PLAYWRIGHT and self._page:
            return await self._search_mobile(keyword, sort_by, max_items)

        return []

    async def _search_mobile(
        self, keyword: str, sort_by: str, max_items: int
    ) -> List[ProductBrief]:
        """移动端搜索 — 拦截 API 获取 JSON 数据。"""

        # 注册 API 拦截器
        self._search_api_responses = []

        # 风控/验证码页面常见标记
        captcha_markers = (
            "验证码", "slider", "nc_wrapper", "passguard",
            "滑动验证", "安全验证", "ufb", "human",
        )

        async def _on_response(resp):
            try:
                if resp.status != 200:
                    return
                if self.SEARCH_API_PATTERN in resp.url:
                    body = await resp.text()
                    self._search_api_responses.append(body)
                elif "main.m.taobao.com/search" in resp.url:
                    # 搜索页本身（非 API）若返回验证页，说明已被风控拦截
                    text = await resp.text()
                    if any(m in text for m in captcha_markers):
                        self._captcha_triggered = True
                        logger.warning("检测到验证码/风控页面，将停止后续抓取")
            except Exception as e:
                logger.debug(f"搜索响应拦截处理异常（已忽略）: {e}")

        self._page.on("response", _on_response)

        # 移动端搜索页（main.m 不会跳下载页）
        search_url = (
            f"https://main.m.taobao.com/search?"
            f"q={quote(keyword)}"
        )

        await self._page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await AntiDetect.human_delay(8, 15)
        await AntiDetect.human_scroll(self._page)

        # 多次滚动触发懒加载
        for _ in range(3):
            await self._page.mouse.wheel(0, random.randint(300, 500))
            await asyncio.sleep(random.uniform(1.0, 2.0))

        # 解析拦截到的 API 数据
        results = []
        seen_ids = set()

        for body in self._search_api_responses:
            items = self._extract_items_from_json(body)
            for item in items:
                item_id = str(item.get("item_id") or item.get("nid") or "")
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

                card = self._parse_card_fields(item)
                name = item.get("title") or item.get("name") or ""
                # 价格：首单价(到手价)优先于裸 price 原价
                price = card["price"] or self._parse_price(
                    item.get("price") or item.get("view_price") or "0"
                )
                img = (
                    item.get("pic_path") or item.get("img")
                    or item.get("image") or item.get("pic_url") or ""
                )
                shop = (
                    item.get("shop") or item.get("nick")
                    or item.get("seller_nick") or ""
                )
                platform = "tmall" if item.get("is_tmall") or item.get("shop_type") == "tmall" else "taobao"

                if name and price > 0:
                    results.append(ProductBrief(
                        platform=platform,
                        platform_id=item_id,
                        name=str(name)[:500],
                        price=Decimal(str(price)),
                        original_price=(
                            Decimal(str(card["original_price"]))
                            if card["original_price"] else None
                        ),
                        shop_name=str(shop),
                        main_image_url=str(img),
                        sales_volume=card["sales_volume"],
                        comment_count=card["comment_count"],
                        product_url=f"https://item.taobao.com/item.htm?id={item_id}",
                        card_extra={
                            "sales_text": card["sales_text"],
                            "comment_snippet": card["comment_snippet"],
                            "rank": card["rank"],
                            "popularity_text": card["popularity_text"],
                            "marketing_usp": card["marketing_usp"],
                            "shop_discount": card["shop_discount"],
                        },
                    ))

        logger.info(f"搜索完成: {len(results)} 个商品")
        return results[:max_items]

    @staticmethod
    def _extract_items_from_json(body: str) -> List[dict]:
        """从 API 响应 JSON 中提取商品列表。"""
        items = []

        # 尝试多种 JSON 结构
        # 结构1: {"data": {"itemsArray": [...]}}  或  {"data": {"items": [...]}}
        # 结构2: {"result": [{"items": [...]}]}
        # 结构3: {"list": [...]}
        # 结构4: 顶层就是数组

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            # JSONP 格式
            m = re.search(r"\{.*\}", body, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except json.JSONDecodeError:
                    return items
            else:
                return items

        def _extract(obj):
            if isinstance(obj, list):
                for item in obj:
                    if isinstance(item, dict) and ("item_id" in item or "title" in item or "price" in item):
                        items.append(item)
            elif isinstance(obj, dict):
                # 优先找已知的商品列表键
                for key in ("itemsArray", "items", "list", "auctions", "itemList", "result"):
                    if key in obj:
                        val = obj[key]
                        if isinstance(val, list):
                            for item in val:
                                if isinstance(item, dict):
                                    items.append(item)
                            return
                # 递归搜索
                for key, val in obj.items():
                    if isinstance(val, (dict, list)):
                        _extract(val)

        _extract(data)
        return items

    # ==================================================================
    # 商品详情
    # ==================================================================

    async def get_detail(self, product: ProductBrief) -> ProductDetail:
        """移动端详情页。"""
        if not self._page or not self._context:
            return ProductDetail(platform_id=product.platform_id)

        # 移动端详情页 URL
        item_url = f"https://main.m.taobao.com/detail/item.htm?id={product.platform_id}"

        try:
            detail_page = await self._context.new_page()
            await detail_page.goto(item_url, wait_until="domcontentloaded", timeout=30000)
            await AntiDetect.human_delay(5, 10)
            html = await detail_page.content()
            await detail_page.close()
            return self._parse_detail_html(html, product.platform_id)
        except Exception as e:
            logger.error(f"详情失败 [{product.platform_id}]: {e}")
            return ProductDetail(platform_id=product.platform_id)

    # ==================================================================
    # 评价
    # ==================================================================

    async def get_reviews(
        self, product: ProductBrief, max_pages: int = 5
    ) -> List[ReviewData]:
        """天猫评价 API（httpx 直调，不需要浏览器）。"""
        reviews = []
        item_id = product.platform_id

        for page in range(1, max_pages + 1):
            try:
                url = (
                    f"https://rate.tmall.com/list_detail_rate.htm?"
                    f"itemId={item_id}&currentPage={page}"
                )
                resp = await self._httpx_client.get(
                    url,
                    headers={"Referer": f"https://detail.tmall.com/item.htm?id={item_id}"},
                )
                self._request_count += 1

                if resp.status_code != 200:
                    break

                m = re.search(r"\{.*\}", resp.text, re.DOTALL)
                if not m:
                    break

                data = json.loads(m.group())
                rate_list = data.get("rateDetail", {}).get("rateList", [])

                for r in rate_list:
                    reviews.append(ReviewData(
                        platform_review_id=str(r.get("id", "")),
                        rating=int(r.get("rate", 5)),
                        content=r.get("rateContent", ""),
                        user_name=r.get("displayUserNick", ""),
                        review_date=(
                            datetime.fromisoformat(r.get("rateDate", ""))
                            .replace(tzinfo=timezone.utc)
                            if r.get("rateDate") else None
                        ),
                        images=[img.get("url", "") for img in r.get("pics", [])],
                        likes=int(r.get("useful", 0)),
                    ))

                if len(rate_list) < 10:
                    break

                await asyncio.sleep(random.uniform(2, 4))

            except Exception as e:
                logger.warning(f"评价失败 [page={page}]: {e}")
                break

        return reviews

    # ==================================================================
    # 内部 — 初始化
    # ==================================================================

    async def _setup_persistent_context(self):
        profile_dir = str(self.PERSISTENT_PROFILE_DIR.resolve())
        logger.info(f"使用持久化浏览器配置: {profile_dir}")

        context = await self._playwright.chromium.launch_persistent_context(
            profile_dir,
            headless=self.headless,
            viewport=self.MOBILE_VIEWPORT,
            user_agent=MOBILE_UA,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            is_mobile=True,
            has_touch=True,
            device_scale_factor=3,
            args=AntiDetect.get_launch_args(),
        )

        self._context = context
        self._page = await context.new_page()
        await AntiDetect.inject_stealth_scripts(self._page)

        cookies = await context.cookies()
        self.cookie_mgr.save(list(cookies))
        logger.info("移动端持久化登录态已加载")

    async def _setup_standalone(self):
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless, args=AntiDetect.get_launch_args()
        )
        self._context = await AntiDetect.create_context(
            self._browser, use_mobile=True
        )
        self._page = await self._context.new_page()
        await AntiDetect.inject_stealth_scripts(self._page)

        cookies = self._load_cookies()
        if cookies:
            await self._context.add_cookies(cookies)

    # ==================================================================
    # 内部 — Cookie
    # ==================================================================

    def _load_cookies(self) -> Optional[List[dict]]:
        cookies = self.cookie_mgr.load()
        if cookies:
            return self._sanitize_cookies(cookies)
        cookies = self.cookie_mgr.load_from_chrome("taobao.com")
        if cookies:
            return self._sanitize_cookies(cookies)
        return None

    def _build_cookie_header(self) -> Optional[str]:
        cookies = self.cookie_mgr.load()
        if not cookies:
            return None
        parts = [f"{c['name']}={c['value']}" for c in cookies if c.get("name") and c.get("value")]
        return "; ".join(parts)

    @staticmethod
    def _sanitize_cookies(cookies: List[dict]) -> List[dict]:
        clean = []
        for c in cookies:
            item = {
                "name": str(c["name"]), "value": str(c["value"]),
                "domain": str(c.get("domain", ".taobao.com")),
                "path": str(c.get("path", "/")),
            }
            exp = c.get("expires")
            if exp is not None and isinstance(exp, (int, float)) and exp > 0:
                item["expires"] = float(exp)
            if c.get("httpOnly"): item["httpOnly"] = True
            if c.get("secure"): item["secure"] = True
            same_site = c.get("sameSite", "Lax")
            item["sameSite"] = same_site if same_site in ("Strict", "Lax", "None") else "Lax"
            clean.append(item)
        return clean

    # ==================================================================
    # 内部 — 解析
    # ==================================================================

    @staticmethod
    def _extract_rank_no(text: str):
        """从榜单文案中提取名次，如 '自行车灯热销榜·第2名' → 2。"""
        idx_d = (text or "").find("第")
        idx_m = (text or "").find("名", idx_d) if idx_d != -1 else -1
        if idx_d == -1 or idx_m == -1:
            return None
        digits = "".join(ch for ch in text[idx_d:idx_m] if ch.isdigit())
        return int(digits) if digits else None

    @staticmethod
    def _first_json_object(text: str):
        """在文本中定位并解析第一个平衡的大括号 JSON 对象。"""
        BACKSLASH = chr(92)
        QUOTE = chr(34)
        start = text.find("{")
        while start != -1:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(text)):
                c = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif c == BACKSLASH:
                        esc = True
                    elif c == QUOTE:
                        in_str = False
                    continue
                if c == QUOTE:
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
            start = text.find("{", start + 1)
        return None

    @staticmethod
    def _extract_embedded_card_json(html: str):
        """从详情页 HTML 的 <script> 中提取含商品卡字段的 JSON 对象。

        淘宝详情页会把部分卡片数据内联进脚本，对应
        references/taobao_product_detail_fields.md 的字段。
        """
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        markers = ("priceShowWithIcon", "realSales", "iconUspSortInfo", "specialUSPInfo")
        for script in soup.find_all("script"):
            txt = script.string or script.get_text() or ""
            if not any(m in txt for m in markers):
                continue
            obj = TaobaoSpider._first_json_object(txt)
            if obj and isinstance(obj, dict) and any(k in obj for k in markers):
                return obj
        return None

    @staticmethod
    def _parse_card_fields(raw: dict) -> dict:
        """从商品卡原始 JSON（搜索/推荐流 itemsArray 单条）提取营销与展示字段。

        对应 references/taobao_product_detail_fields.md：
        - priceShowWithIcon.price              → 到手价(首单价)
        - priceShowWithIcon.originPrice/price  → 原价(划线价)
        - realSales                            → 销量文案 + 销量数字
        - iconUspSortInfo.numericalCommentInfo  → 评价数 + 评价摘录
        - iconUspSortInfo.tmRankInfo           → 热销榜(名称/名次/跳转)
        - iconUspSortInfo.extend_source        → 人气热度文案
        - specialUSPInfo / shopDiscountInfo    → 营销利益点(USP)
        """
        raw = raw or {}
        # ---- 价格 ----
        psi = raw.get("priceShowWithIcon") or {}
        price = TaobaoSpider._parse_price(psi.get("price") or raw.get("price") or "0")
        orig_raw = psi.get("originPrice")  # 无划线价保持 None，不回退到当前价
        original_price = TaobaoSpider._parse_price(orig_raw) if orig_raw else None
        # ---- 销量 ----
        raw_sales = raw.get("realSales") or raw.get("sold") or raw.get("sales") or ""
        sales_volume = TaobaoSpider._parse_int(str(raw_sales))
        # ---- 评价 / 榜单 / 热度 ----
        rank = None
        popularity_text = ""
        comment_snippet = ""
        comment_count = 0
        for usp in (raw.get("iconUspSortInfo") or []):
            if not isinstance(usp, dict):
                continue
            code = usp.get("usp_code", "")
            text = usp.get("text", "") or ""
            if code == "numericalCommentInfo":
                comment_count = TaobaoSpider._parse_int(text)
                ci = text.find("评价")
                if ci != -1:
                    rest = text[ci + 2:].lstrip("“\"'")
                    end = len(rest)
                    for j, ch in enumerate(rest):
                        if ch in ("”", '"', "'", "，"):
                            end = j
                            break
                    comment_snippet = rest[:end].strip()
            elif code == "tmRankInfo":
                rank = {
                    "text": text,
                    "url": usp.get("url", ""),
                    "no": TaobaoSpider._extract_rank_no(text),
                    "rank_type": usp.get("rankType", ""),
                }
            elif code == "extend_source":
                popularity_text = text
        # ---- 营销 USP ----
        marketing_usp = [
            {
                "code": u.get("usp_code", ""),
                "text": u.get("text", ""),
                "icon": u.get("icon", ""),
            }
            for u in (raw.get("specialUSPInfo") or [])
            if isinstance(u, dict)
        ]
        shop_discount = raw.get("shopDiscountInfo") or (
            marketing_usp[0]["text"] if marketing_usp else ""
        )
        return {
            "price": price,
            "original_price": original_price,
            "sales_volume": sales_volume,
            "sales_text": str(raw_sales),
            "comment_count": comment_count,
            "comment_snippet": comment_snippet,
            "rank": rank,
            "popularity_text": popularity_text,
            "marketing_usp": marketing_usp,
            "shop_discount": shop_discount,
        }

    def _parse_detail_html(self, html: str, platform_id: str) -> ProductDetail:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        brand = None
        brand_elem = soup.select_one("[class*='brand'], .J_EbrandLogo")
        if brand_elem:
            brand = brand_elem.get("title") or brand_elem.get_text(strip=True)

        specs = {}
        for s in soup.select(".J_TSaleProp li, .tb-skin li, [data-property]"):
            key = s.get("data-property") or s.select_one(".name")
            val = s.select_one(".value") or s
            if key and val:
                name = key.get_text(strip=True) if hasattr(key, "get_text") else str(key)
                specs[name] = val.get_text(strip=True)

        detail_images = []
        for img in soup.select("#J_DivItemDesc img, .detail-img img"):
            src = img.get("data-src") or img.get("src") or ""
            if src.startswith("http"):
                detail_images.append(src)

        # 从详情页内联脚本提取商品卡字段（首单价/原价/榜单/热度/营销USP）
        # 注意：这些字段写入独立的 marketing 列，不污染 specs（规格参数）
        # 榜单(rank)单独提升到 hot_rankings 表，不放在 marketing 里
        original_price = None
        marketing = {}
        card_raw = self._extract_embedded_card_json(html)
        if card_raw:
            card = self._parse_card_fields(card_raw)
            if card["original_price"]:
                original_price = Decimal(str(card["original_price"]))
            for k, v in {
                "sales_text": card["sales_text"],
                "comment_snippet": card["comment_snippet"],
                "popularity_text": card["popularity_text"],
                "marketing_usp": card["marketing_usp"],
                "shop_discount": card["shop_discount"],
            }.items():
                if v not in (None, "", [], {}, False):
                    marketing[k] = v

        return ProductDetail(
            platform_id=platform_id, brand=brand,
            specs=specs, detail_images=detail_images,
            original_price=original_price,
            marketing=marketing,
        )

    # ==================================================================
    # 工具
    # ==================================================================

    @staticmethod
    def _parse_price(text: str) -> float:
        text = re.sub(r"[^\d.]", "", str(text))
        try: return float(text)
        except ValueError: return 0.0

    @staticmethod
    def _parse_int(text: str) -> int:
        text = str(text).strip()
        m = re.match(r"([\d.]+)万", text)
        if m: return int(float(m.group(1)) * 10000)
        nums = re.sub(r"[^\d]", "", text)
        return int(nums) if nums else 0
