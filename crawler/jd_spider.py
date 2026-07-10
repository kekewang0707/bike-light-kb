"""京东平台爬虫 — API 优先 + CDP 连接 + XHR 拦截评价。

采用三级降级策略：

Level 1 (主力): 移动端 API (api.m.jd.com)
  - 搜索: 200-300ms/页, JSON 响应
  - 限流: 约 30次/分钟
  - 降级条件: 403/429/签名不对

Level 2 (备选): Playwright CDP 连接
  - 搜索/详情: 3-4s/页, 完整 JS 渲染
  - 评价: XHR 响应拦截（绕过签名）
  - 降级条件: 验证码触发

Level 3 (兜底): httpx HTML 解析
  - 仅限搜索列表
  - 数据不完整, 仅作最后手段

Usage::

    from crawler.jd_spider import JDSpider

    spider = JDSpider(use_cdp=True)
    await spider.setup()

    # 搜索
    products = await spider.search("自行车灯", max_items=50)

    # 详情
    detail = await spider.get_detail(products[0])

    # 评价
    reviews = await spider.get_reviews(products[0], max_pages=3)

    await spider.teardown()
"""

import asyncio
import json
import re
import time
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from crawler.anti_detect import AntiDetect, CDPConnection, LoginHelper
from crawler.base import BaseSpider
from crawler.cookie_manager import CookieManager
from crawler.parser import (
    parse_price,
    parse_int,
    parse_good_rate,
    clean_html,
    extract_jd_sku_id,
)
from crawler.proxy_pool import ProxyPool
from crawler.schemas import (
    ProductBrief,
    ProductDetail,
    SkuData,
    ReviewData,
    PricePoint,
)

# Playwright 可选导入
try:
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


class JDSpider(BaseSpider):
    """京东平台爬虫 — 混合架构。

    参数:
        use_cdp:         True 通过 CDP 连接本地 Chrome (推荐), False 启动独立 Playwright
        use_mobile_api:  True 优先使用移动端 API 搜索 (更快), False 仅用 Playwright
        headless:        False (京东检测极严, 有头模式为必须)
        proxy_pool:      ProxyPool 实例, None 则直连
        cookie_manager:  CookieManager 实例
    """

    platform = "jd"

    # ---- API 端点 ----
    SEARCH_API = "https://api.m.jd.com/client.action"
    DETAIL_API = "https://api.m.jd.com/client.action"

    # ---- 用于构建 URL ----
    JD_SEARCH_URL = "https://search.jd.com/Search"
    JD_ITEM_URL = "https://item.jd.com/{sku_id}.html"
    JD_M_SEARCH_URL = "https://m.jd.com/search"

    def __init__(
        self,
        use_cdp: bool = True,
        use_mobile_api: bool = True,
        headless: bool = False,
        proxy_pool: Optional[ProxyPool] = None,
        cookie_manager: Optional[CookieManager] = None,
    ):
        self.use_cdp = use_cdp
        self.use_mobile_api = use_mobile_api
        self.headless = headless
        self.proxy_pool = proxy_pool or ProxyPool()
        self.cookie_mgr = cookie_manager or CookieManager(platform="jd")

        # 运行时状态
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._httpx_client: Optional[httpx.AsyncClient] = None
        self._request_count = 0
        self._start_time = 0.0
        self._captcha_triggered = False

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def setup(self):
        """初始化爬虫 — 连接浏览器、加载 Cookie。"""
        self._start_time = time.time()
        self._request_count = 0
        self._captcha_triggered = False

        if HAS_PLAYWRIGHT:
            self._playwright = await async_playwright().start()

            if self.use_cdp:
                # CDP 连接（首选）
                try:
                    self._browser = await CDPConnection.connect_to_local_chrome()
                    logger.info("✅ CDP 连接成功")
                except RuntimeError as e:
                    logger.warning(f"CDP 连接失败: {e}，回退到独立 Playwright")
                    self._browser = await self._launch_browser()
            else:
                self._browser = await self._launch_browser()

            proxy = self.proxy_pool.get_next()
            self._context = AntiDetect.create_context(self._browser, proxy=proxy)
            self._page = await self._context.new_page()
            await AntiDetect.inject_stealth_scripts(self._page)

            # 加载 Cookie
            cookies = self.cookie_mgr.load()
            if cookies:
                await self._context.add_cookies(cookies)
                logger.info(f"已加载 {len(cookies)} 个 Cookie")
            elif self.use_cdp:
                # CDP 连接复用本地 Chrome 的 Cookie，无需手动加载
                logger.info("CDP 模式 — 使用本地 Chrome Cookie")
            else:
                logger.warning("无可用 Cookie，部分数据可能不完整")

        # httpx 客户端（API 路径用）
        proxy_config = self.proxy_pool.get_next()
        self._httpx_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
            headers={
                "User-Agent": AntiDetect.random_ua(mobile=True),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://m.jd.com/",
            },
            proxy=proxy_config["server"] if proxy_config else None,
        )

    async def teardown(self):
        """清理资源。"""
        if self._httpx_client:
            await self._httpx_client.aclose()
            self._httpx_client = None

        if self._page:
            # 保存 Cookie
            try:
                cookies = await self._context.cookies()
                self.cookie_mgr.save(cookies)
            except Exception:
                pass

        if self._context:
            await self._context.close()
            self._context = None

        if self._browser:
            await self._browser.close()
            self._browser = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    # ==================================================================
    # 搜索 (API 优先 → Playwright 降级 → HTML 兜底)
    # ==================================================================

    async def search(
        self,
        keyword: str,
        sort_by: str = "sales",
        max_items: int = 100,
    ) -> List[ProductBrief]:
        """搜索京东商品列表 — 三种路径降级。"""
        logger.info(f"搜索: {keyword!r} (sort={sort_by}, max={max_items})")

        # Level 1: 移动端 API
        if self.use_mobile_api:
            try:
                results = await self._search_via_api(keyword, sort_by, max_items)
                if results:
                    logger.info(f"API 搜索成功: {len(results)} 个商品")
                    return results
                logger.info("API 返回空结果，降级到 Playwright")
            except Exception as e:
                logger.warning(f"API 搜索失败: {e}，降级到 Playwright")

        # Level 2: Playwright
        if HAS_PLAYWRIGHT and self._page:
            try:
                results = await self._search_via_playwright(keyword, sort_by, max_items)
                if results:
                    logger.info(f"Playwright 搜索成功: {len(results)} 个商品")
                    return results
            except Exception as e:
                logger.warning(f"Playwright 搜索失败: {e}，降级到 HTML")

        # Level 3: HTML 兜底
        try:
            results = await self._search_via_html(keyword, sort_by, max_items)
            logger.info(f"HTML 搜索: {len(results)} 个商品")
            return results
        except Exception as e:
            logger.error(f"所有搜索路径均失败: {e}")
            return []

    async def _search_via_api(
        self, keyword: str, sort_by: str, max_items: int
    ) -> List[ProductBrief]:
        """Level 1: 移动端 API 搜索。

        调用 api.m.jd.com 的搜索接口。
        注意: 此接口可能需要签名参数，如果签名过期会返回 403。
        """
        results = []
        page = 1
        collected = 0

        while collected < max_items:
            if self._captcha_triggered:
                break

            await AntiDetect.human_delay(1.0, 2.0)

            try:
                params = {
                    "functionId": "search",
                    "body": json.dumps({
                        "keyword": keyword,
                        "page": page,
                        "pageSize": min(30, max_items - collected),
                        "sortType": self._map_sort_type(sort_by),
                    }),
                    "client": "apple",
                    "clientVersion": "12.0.0",
                    "loginType": "2",
                }

                resp = await self._httpx_client.post(
                    self.SEARCH_API,
                    params=params,
                )
                self._request_count += 1

                if resp.status_code == 403 or resp.status_code == 429:
                    logger.warning(f"API 限流 (HTTP {resp.status_code})，切换到 Playwright")
                    break

                if resp.status_code != 200:
                    logger.warning(f"API 返回 {resp.status_code}，切换到 Playwright")
                    break

                data = resp.json()
                products = self._parse_api_search_result(data)
                if not products:
                    break

                results.extend(products)
                collected = len(results)
                page += 1

            except httpx.TimeoutException:
                logger.warning("API 搜索超时")
                break
            except Exception as e:
                logger.warning(f"API 搜索异常: {e}")
                break

        return results[:max_items]

    async def _search_via_playwright(
        self, keyword: str, sort_by: str, max_items: int
    ) -> List[ProductBrief]:
        """Level 2: Playwright 搜索 — 打开京东搜索页并解析结果。"""
        if not self._page:
            return []

        # 京东移动端搜索页
        search_url = (
            f"https://search.jd.com/Search?"
            f"keyword={quote(keyword)}&enc=utf-8&"
            f"psort={self._map_sort_type(sort_by)}"
        )

        await self._page.goto(search_url, wait_until="domcontentloaded")
        await AntiDetect.human_delay(3, 6)
        await AntiDetect.human_scroll(self._page)

        # 检测验证码
        if await AntiDetect.detect_captcha(self._page):
            self._captcha_triggered = True
            return []

        # 多次滚动以加载更多商品
        for _ in range(3):
            await self._page.mouse.wheel(0, 800)
            await asyncio.sleep(random.uniform(0.5, 1.0))

        html = await self._page.content()
        return self._parse_html_search_result(html)[:max_items]

    async def _search_via_html(
        self, keyword: str, sort_by: str, max_items: int
    ) -> List[ProductBrief]:
        """Level 3: HTML 兜底 — 直接 GET 搜索页 HTML。"""
        search_url = (
            f"https://search.jd.com/Search?"
            f"keyword={quote(keyword)}&enc=utf-8"
        )
        resp = await self._httpx_client.get(search_url)
        self._request_count += 1

        if resp.status_code != 200:
            logger.error(f"HTML 搜索失败 (HTTP {resp.status_code})")
            return []

        return self._parse_html_search_result(resp.text)[:max_items]

    # ==================================================================
    # 商品详情 (Playwright)
    # ==================================================================

    async def get_detail(self, product: ProductBrief) -> ProductDetail:
        """获取商品详情 — Playwright 渲染后提取。

        由于京东详情页规格区域需要 JS 渲染，不用 httpx。
        """
        if not self._page:
            logger.warning("Playwright 不可用，返回空详情")
            return ProductDetail(platform_id=product.platform_id)

        item_url = product.product_url or self._build_item_url(product.platform_id)
        logger.debug(f"抓取详情: {item_url}")

        try:
            await self._page.goto(item_url, wait_until="domcontentloaded")
            await AntiDetect.human_delay(3, 6)
            self._request_count += 1

            # 检测验证码
            if await AntiDetect.detect_captcha(self._page):
                self._captcha_triggered = True
                return ProductDetail(platform_id=product.platform_id)

            html = await self._page.content()
            soup = BeautifulSoup(html, "lxml")

            # ---- 提取品牌 ----
            brand = None
            brand_elem = soup.select_one('#parameter-brand li[title], .parameter-brand a[title]')
            if brand_elem:
                brand = brand_elem.get("title", "").strip()

            # ---- 提取规格参数 ----
            specs = {}
            spec_items = soup.select('#detail .parameter-brand li, ul.parameter2 li')
            for item in spec_items:
                text = clean_html(str(item))
                if "：" in text or ":" in text:
                    sep = "：" if "：" in text else ":"
                    key, _, value = text.partition(sep)
                    key = key.strip()
                    value = value.strip()
                    if key and value and value not in ("暂无", "-", "--"):
                        specs[key] = value

            # ---- 提取分类 ----
            category = None
            breadcrumb = soup.select_one('.breadcrumb, #J_crumb')
            if breadcrumb:
                category = clean_html(breadcrumb.get_text())[-50:]  # 取最后一级分类

            # ---- 提取详情图 ----
            detail_images = []
            img_elements = soup.select('#J-detail-content img, div.detail-content img')
            for img in img_elements:
                src = img.get("src") or img.get("data-src") or img.get("data-lazy-img")
                if src and src.startswith("http"):
                    # 过滤掉占位图
                    if any(skip in src for skip in ["white.gif", "placeholder", "1x1"]):
                        continue
                    detail_images.append(src)

            # ---- 提取 SKU ----
            skus = await self._extract_skus()

            return ProductDetail(
                platform_id=product.platform_id,
                brand=brand,
                category=category,
                specs=specs,
                skus=skus,
                detail_images=detail_images,
                original_price=product.original_price if hasattr(product, 'original_price') else None,
            )

        except Exception as e:
            logger.error(f"详情抓取失败 [{product.platform_id}]: {e}")
            return ProductDetail(platform_id=product.platform_id)

    # ==================================================================
    # 评价采集 (XHR 拦截)
    # ==================================================================

    async def get_reviews(
        self, product: ProductBrief, max_pages: int = 5
    ) -> List[ReviewData]:
        """获取评价 — Playwright XHR 拦截（绕过 club.jd.com 签名）。

        原理:
        1. 打开商品详情页
        2. 点击"商品评价" tab
        3. 页面自己发 XHR 请求到 club.jd.com（带正确签名）
        4. 我们通过 page.on("response") 拦截并解析响应
        5. 点击"下一页"，重复 3-4
        """
        if not self._page:
            logger.warning("Playwright 不可用，无法采集评价")
            return []

        item_url = product.product_url or self._build_item_url(product.platform_id)
        logger.debug(f"采集评价: {item_url} (最多 {max_pages} 页)")

        reviews: List[ReviewData] = []
        collected_pages = 0

        # 用于从 XHR 响应 URL 中提取 productId
        xhr_product_id = None

        async def on_response(response):
            nonlocal xhr_product_id
            url = response.url

            # 拦截评价接口的 XHR 响应
            if "club.jd.com/comment/skuProductPageComments" not in url:
                return

            # 从 URL 提取 productId
            if not xhr_product_id:
                match = re.search(r'productId=(\d+)', url)
                if match:
                    xhr_product_id = match.group(1)

            try:
                body = await response.json()
                comments = self._parse_xhr_comments(body)
                for c in comments:
                    reviews.append(c)
                nonlocal collected_pages
                collected_pages += 1
                logger.debug(f"XHR 拦截: 第 {collected_pages} 页, {len(comments)} 条评价")
            except Exception:
                pass  # 解析失败不阻塞

        try:
            self._page.on("response", on_response)

            await self._page.goto(item_url, wait_until="domcontentloaded")
            await AntiDetect.human_delay(3, 5)
            self._request_count += 1

            # 检测验证码
            if await AntiDetect.detect_captcha(self._page):
                self._captcha_triggered = True
                return reviews

            # 点击"商品评价" tab
            clicked = False
            for selector in [
                'li[data-tab="comment"]',
                'a:has-text("商品评价")',
                '.tab-main li:has-text("评价")',
                '#detail .tab-main li:has-text("评价")',
            ]:
                try:
                    tab = self._page.locator(selector).first
                    if await tab.count() > 0:
                        await tab.click()
                        await AntiDetect.human_delay(2, 4)
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                logger.warning(f"无法找到评价 tab [{product.platform_id}]")

            # 翻页
            for _ in range(max_pages - 1):
                if self._captcha_triggered:
                    break

                try:
                    next_btn = self._page.locator('a.jp-next, .ui-pager-next, a:has-text("下一页")').first
                    if await next_btn.count() == 0:
                        break
                    if await next_btn.get_attribute("class"):
                        classes = await next_btn.get_attribute("class") or ""
                        if "disabled" in classes or "ui-pager-disabled" in classes:
                            break

                    await next_btn.click()
                    await AntiDetect.human_delay(3, 6)
                except Exception:
                    break

            # 等待最后的 XHR 完成
            await asyncio.sleep(2)

        finally:
            self._page.remove_listener("response", on_response)

        logger.info(f"评价采集完成: {len(reviews)} 条 (product={product.platform_id})")
        return reviews

    # ==================================================================
    # 历史价格 (暂不实现)
    # ==================================================================

    async def get_price_history(self, product: ProductBrief) -> List[PricePoint]:
        """京东历史价格 — 目前无稳定公开接口。"""
        return []

    # ==================================================================
    # 解析方法
    # ==================================================================

    def _parse_api_search_result(self, data: dict) -> List[ProductBrief]:
        """解析移动端 API 搜索响应。"""
        results = []

        # 京东 API 响应结构可能有多种包装
        goods_list = (
            data.get("data", {})
            or data.get("result", {})
            or data
        )

        if isinstance(goods_list, dict):
            goods_list = goods_list.get("goodsList") or goods_list.get("products") or []

        if not isinstance(goods_list, list):
            return results

        for item in goods_list:
            try:
                sku_id = str(item.get("skuId") or item.get("wareId") or item.get("id", ""))
                if not sku_id:
                    continue

                brief = ProductBrief(
                    platform="jd",
                    platform_id=sku_id,
                    name=str(item.get("wareName") or item.get("title") or item.get("name", "")),
                    price=parse_price(str(item.get("jdPrice") or item.get("price", "0"))) or Decimal("0"),
                    shop_name=str(item.get("shopName") or item.get("shop_name", "")),
                    main_image_url=str(item.get("imageurl") or item.get("imgUrl") or item.get("image", "")),
                    sales_volume=parse_int(str(item.get("inOrderCount30Days") or item.get("sales", "0"))) or 0,
                    comment_count=parse_int(str(item.get("commentCount") or item.get("comments", "0"))) or 0,
                    good_rate=parse_good_rate(str(item.get("goodRate") or item.get("good_rate", ""))),
                    product_url=self._build_item_url(sku_id),
                )
                results.append(brief)
            except Exception as e:
                logger.debug(f"解析搜索项失败: {e}")
                continue

        return results

    def _parse_html_search_result(self, html: str) -> List[ProductBrief]:
        """解析搜索页 HTML。"""
        results = []
        soup = BeautifulSoup(html, "lxml")

        items = soup.select('.gl-item, li.gl-item, div[data-sku]')
        for item in items:
            try:
                sku_id = item.get("data-sku", "")
                if not sku_id:
                    continue

                # 标题
                name_elem = item.select_one('.p-name em, .p-name a, a[title]')
                name = ""
                if name_elem:
                    name = clean_html(name_elem.get_text())
                    if not name:
                        name = name_elem.get("title", "")

                # 价格
                price_elem = item.select_one('.p-price strong, .p-price i')
                price = parse_price(clean_html(price_elem.get_text())) if price_elem else None

                # 店铺
                shop_elem = item.select_one('.p-shop a, .p-shop span')
                shop = clean_html(shop_elem.get_text()) if shop_elem else ""

                # 图片
                img_elem = item.select_one('img[data-sku], .p-img img')
                img_url = ""
                if img_elem:
                    img_url = img_elem.get("src") or img_elem.get("data-lazy-img") or ""

                # 评价数
                comment_elem = item.select_one('.p-commit a, .p-commit strong')
                comment_count = parse_int(clean_html(comment_elem.get_text())) if comment_elem else 0

                if name and price:
                    results.append(ProductBrief(
                        platform="jd", platform_id=sku_id, name=name,
                        price=price, shop_name=shop, main_image_url=img_url,
                        sales_volume=0, comment_count=comment_count,
                        product_url=self._build_item_url(sku_id),
                    ))
            except Exception:
                continue

        return results

    def _parse_xhr_comments(self, data: dict) -> List[ReviewData]:
        """解析 club.jd.com XHR 评价响应。"""
        reviews = []
        comments = data.get("comments") or data.get("data", {}).get("comments") or []

        for c in comments:
            try:
                # 图片
                images = []
                for img_data in c.get("images", []) or []:
                    img_url = img_data.get("imgUrl") or img_data.get("imageUrl", "")
                    if img_url and not img_url.startswith("http"):
                        img_url = f"https:{img_url}"
                    if img_url:
                        images.append(img_url)

                review_date = None
                creation_time = c.get("creationTime") or c.get("createTime", "")
                if creation_time:
                    try:
                        review_date = datetime.fromisoformat(creation_time.replace("Z", "+00:00"))
                    except Exception:
                        pass

                reviews.append(ReviewData(
                    platform_review_id=str(c.get("id") or c.get("guid", "")),
                    rating=int(c.get("score") or c.get("star") or 5),
                    content=str(c.get("content") or c.get("comment", "")),
                    user_name=str(c.get("nickname") or c.get("userName", "")),
                    user_level=str(c.get("userLevelName") or c.get("userLevel", "")) or None,
                    review_date=review_date,
                    images=images,
                    likes=int(c.get("usefulVoteCount") or c.get("likeCount", 0)),
                    reply_content=str(c.get("replyContent") or c.get("afterContent", "")) or None,
                ))
            except Exception:
                continue

        return reviews

    # ==================================================================
    # SKU 提取
    # ==================================================================

    async def _extract_skus(self) -> List[SkuData]:
        """从详情页提取 SKU 信息。

        策略: 执行 JS 获取 window._itemInfo 或者解析 SKU 切换区域的 DOM。
        """
        skus = []
        try:
            # 尝试从 JS 变量获取（最可靠）
            item_info = await self._page.evaluate("() => window._itemInfo || window.itemInfo || null")
            if item_info:
                # itemInfo 结构因页面而异，这里做通用处理
                color_size_list = (
                    item_info.get("colorSize")
                    or item_info.get("skuList")
                    or []
                )
                for cs in color_size_list:
                    skus.append(SkuData(
                        sku_name=str(cs.get("name", cs.get("skuName", ""))),
                        price=parse_price(str(cs.get("price", "0"))) or Decimal("0"),
                        stock=int(cs.get("stock", cs.get("stockNum", 0))),
                        sku_specs=cs.get("specs", cs.get("skuSpecs", {})),
                    ))
                return skus
        except Exception:
            pass

        # 回退: 解析 DOM 中的 SKU 切换按钮
        try:
            sku_buttons = await self._page.query_selector_all(
                '.sku-line .sku-item, .choose-item .item, [data-sku]'
            )
            for btn in sku_buttons[:20]:  # 限制最大 SKU 数
                try:
                    name = await btn.get_attribute("title") or await btn.text_content() or ""
                    data_sku = await btn.get_attribute("data-sku") or ""
                    skus.append(SkuData(
                        sku_name=name.strip(),
                        price=Decimal("0"),
                        stock=0,
                        sku_specs={"data_sku": data_sku},
                    ))
                except Exception:
                    continue
        except Exception:
            pass

        return skus

    # ==================================================================
    # 内部辅助
    # ==================================================================

    async def _launch_browser(self) -> "Browser":
        """启动独立的 Playwright Chromium。"""
        if not HAS_PLAYWRIGHT:
            raise RuntimeError("请安装 playwright: pip install playwright && playwright install")

        browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=AntiDetect.get_launch_args(),
        )
        return browser

    @staticmethod
    def _build_item_url(sku_id: str) -> str:
        """构建京东商品详情页 URL。"""
        if sku_id.isdigit():
            return f"https://item.jd.com/{sku_id}.html"
        return ""

    @staticmethod
    def _map_sort_type(sort_by: str) -> str:
        """映射排序方式到京东参数值。"""
        mapping = {
            "sales": "sort_totalsales15_desc",
            "price_asc": "sort_price_asc",
            "price_desc": "sort_price_desc",
            "default": "",
        }
        return mapping.get(sort_by, "sort_totalsales15_desc")
