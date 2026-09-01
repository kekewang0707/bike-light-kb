"""淘宝扫码登录、Cookie 持久化与带 Cookie 请求（独立功能模块）。

设计目标：**只在第一次扫码时取一次登录态 Cookie，之后做逆向（API 请求）完全依赖这份 Cookie 文件**，
不保留任何浏览器持久化 profile（不会生成/使用 data/browser_taobao_profile）。

- 登录：一次性扫码 → 提取登录态 Cookie → 保存到 data/cookies/taobao_cookies.json → 关闭浏览器。
- 逆向：读出这份 Cookie，用它拼 Cookie 头 / 签名参数，发起带登录态的淘宝请求。

复用现成的 CookieManager 做持久化（与爬虫、Chrome 导入共用同一份 Cookie 文件），
保持项目约定：loguru 日志、UTC 时间、移动端 UA。

用法::

    import asyncio
    from crawler.taobao_auth import login_and_save, request, check_login, load_cookies, cookie_dict

    # 1) 首次扫码登录（一次性，取完 Cookie 即关浏览器，不留 profile）
    cookies = asyncio.run(login_and_save())

    # 2) 读取 Cookie（逆向用）
    ck = load_cookies()
    d = cookie_dict()            # {"cookie2": "...", "unb": "...", ...}
    ok = asyncio.run(check_login())   # 本地校验登录态

    # 3) 用 Cookie 发起带登录态的请求
    resp = asyncio.run(request("https://main.m.taobao.com/", method="GET"))
    print(resp.status_code, resp.text[:200])
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import httpx
from loguru import logger

from crawler.cookie_manager import CookieManager

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# 复用得较少的启动参数；若 anti_detect 不可用则退化为空参。
try:
    from crawler.anti_detect import AntiDetect
except Exception:  # pragma: no cover
    class AntiDetect:
        @staticmethod
        def get_launch_args() -> list:
            return []


# ---------------------------------------------------------------------------
# 常量（与 taobao_spider 保持一致）
# ---------------------------------------------------------------------------

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Mobile/15E148 Safari/604.1"
)

MOBILE_VIEWPORT = {"width": 390, "height": 844}

# 桌面端（扫码登录用，PC 登录页默认展示二维码，更稳定）
DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
DESKTOP_VIEWPORT = {"width": 1280, "height": 800}

# 登录后会出现的关键 Cookie 名：cookie2 为建立会话的必备项；
# tracknick / unb / lgc 之一可视为已登录用户
_LOGIN_MARKERS = {"cookie2"}
_LOGIN_USER_MARKERS = {"tracknick", "unb", "lgc"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_logged_in(cookies: List[dict]) -> bool:
    """根据 Cookie 集合判断是否已建立登录态。"""
    if not cookies:
        return False
    names = {c.get("name") for c in cookies if c.get("name")}
    if not (_LOGIN_MARKERS & names):
        return False
    return bool(_LOGIN_USER_MARKERS & names)


async def _build_context(browser) -> Any:
    """创建桌面端浏览器上下文（PC 扫码登录用，非持久化）。"""
    return await browser.new_context(
        viewport=DESKTOP_VIEWPORT,
        user_agent=DESKTOP_UA,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )


async def _try_switch_to_qr(page) -> None:
    """尽力把登录页切换到"扫码登录" tab。

    登录页默认可能是"密码登录"，这里尝试点一下"扫码登录"入口让二维码显示。
    全部失败也静默忽略（多数情况下登录页已默认展示扫码，或由用户自行切换），
    绝不因这一步失败打断登录流程。
    """
    try:
        await page.wait_for_timeout(2500)  # 等前端框架渲染完成
    except Exception:
        pass

    candidates = [
        "text=扫码登录",
        "text=扫码",
        ".qrcode-login",
        "[class*=qrLogin]",
        "[class*=qrcode]",
        "#qrcode-login",
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=2500):
                await loc.click(timeout=5000)
                return
        except Exception:
            continue


# ---------------------------------------------------------------------------
# 1) 一次性扫码登录 + 保存 Cookie
# ---------------------------------------------------------------------------

async def login_and_save(
    cookie_mgr: Optional[CookieManager] = None,
    timeout_minutes: int = 10,
) -> List[dict]:
    """移动端扫码登录（一次性），并保存登录态 Cookie。

    只做一件事：扫码 → 取登录态 Cookie → 写进 data/cookies/taobao_cookies.json → 关闭浏览器。
    **不会创建或复用浏览器持久化 profile**，因此不会生成 data/browser_taobao_profile。
    后续逆向请求直接读取这份 Cookie 文件即可。

    流程：
    1. 用非持久化桌面端（PC）浏览器上下文打开浏览器
    2. 打开 login.taobao.com（PC 扫码登录页，默认展示二维码），等待用户扫码登录
    3. 轮询检测登录态 Cookie（cookie2 + 用户标记）；检测到即停止，或用户关闭浏览器窗口结束
    4. 检测到登录后，把 Cookie 通过 CookieManager 保存到 data/cookies/taobao_cookies.json

    参数:
        cookie_mgr:      自定义 CookieManager（默认 platform=taobao）。
        timeout_minutes: 最长等待扫码时间（分钟）。默认 10。

    返回:
        Cookie 列表；若最终未检测到登录，返回 [] 且不会覆盖已保存的 cookie。
    """
    if not HAS_PLAYWRIGHT:
        raise RuntimeError(
            "未安装 playwright，请先: pip install playwright && playwright install chromium"
        )

    cookie_mgr = cookie_mgr or CookieManager(platform="taobao")

    logger.info("=" * 50)
    logger.info("淘宝 PC 端扫码登录（一次性，仅取 Cookie）")
    logger.info("请在浏览器中扫码；检测到登录会自动保存并关闭，或直接关窗结束")
    logger.info(f"Cookie 将保存到: {cookie_mgr.cookie_file}")
    logger.info("=" * 50)

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=False, args=AntiDetect.get_launch_args()
    )
    context = await _build_context(browser)
    page = context.pages[0] if context.pages else await context.new_page()

    try:
        # 尽量注入 stealth 脚本（可选；失败不影响登录）
        if hasattr(AntiDetect, "inject_stealth_scripts"):
            try:
                await AntiDetect.inject_stealth_scripts(page)
            except Exception:
                pass

        await page.goto(
            "https://login.taobao.com/member/login.jhtml",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        # PC 登录页默认展示扫码；若落在密码登录 tab，尽力切到"扫码登录"（失败不影响）
        await _try_switch_to_qr(page)

        deadline = time.time() + timeout_minutes * 60
        logged_in = False
        last_cookies: List[dict] = []

        while time.time() < deadline:
            try:
                last_cookies = await context.cookies()
            except Exception:
                last_cookies = []
            if _is_logged_in(last_cookies):
                logged_in = True
                break
            if page.is_closed():
                break
            await asyncio.sleep(2)

        cookies = list(last_cookies)
        if not cookies and not page.is_closed():
            try:
                cookies = await context.cookies()
            except Exception:
                pass

        if logged_in and _is_logged_in(cookies):
            cookie_mgr.save(cookies)
            logger.info(
                f"✅ 登录成功，已保存 {len(cookies)} 个 Cookie → {cookie_mgr.cookie_file}"
            )
            return cookies

        logger.warning("未检测到有效登录（可能未扫码完成或未登录）。已保存的 Cookie 未被覆盖。")
        return []

    finally:
        # 关闭临时浏览器，不保留任何 web 会话
        try:
            await context.close()
        except Exception:
            pass
        try:
            await browser.close()
        except Exception:
            pass
        try:
            await pw.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 2) 读取 Cookie
# ---------------------------------------------------------------------------

def load_cookies(cookie_mgr: Optional[CookieManager] = None) -> Optional[List[dict]]:
    """读取已保存的淘宝 Cookie（含过期校验）。

    参数:
        cookie_mgr: 自定义 CookieManager（默认 platform=taobao）。

    返回:
        Cookie 列表；文件不存在、已过期或格式错误时返回 None。
    """
    cookie_mgr = cookie_mgr or CookieManager(platform="taobao")
    return cookie_mgr.load()


def cookie_dict(cookie_mgr: Optional[CookieManager] = None) -> Dict[str, str]:
    """把保存的 Cookie 转成 {name: value} 字典，便于逆向拼签名参数。

    参数:
        cookie_mgr: 自定义 CookieManager（默认 platform=taobao）。

    返回:
        {name: value} 字典；无 Cookie 时返回空字典。
    """
    cookies = load_cookies(cookie_mgr) or []
    return {
        c["name"]: c["value"]
        for c in cookies
        if c.get("name") and c.get("value")
    }


def cookie_header(cookies: Optional[List[dict]] = None) -> str:
    """把 Cookie 列表拼成 HTTP Cookie 头字符串（供 httpx 请求使用）。

    参数:
        cookies: Cookie 列表；为空时自动从保存文件加载。

    返回:
        "name=value; ..." 字符串；无 Cookie 时返回空串。
    """
    if cookies is None:
        cookies = load_cookies() or []
    parts = [
        f"{c['name']}={c['value']}"
        for c in cookies
        if c.get("name") and c.get("value")
    ]
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# 3) 带 Cookie 请求淘宝
# ---------------------------------------------------------------------------

async def request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json_body: Any = None,
    data: Any = None,
    cookies: Optional[List[dict]] = None,
    cookie_mgr: Optional[CookieManager] = None,
) -> httpx.Response:
    """用已保存的 Cookie 发起带登录态的淘宝请求（httpx 异步）。

    参数:
        url:         目标 URL（完整地址，或相对 path 会拼到 https://main.m.taobao.com/）。
        method:      HTTP 方法，默认 GET。
        headers:     额外请求头。
        params:      URL 查询参数。
        json_body:   JSON 请求体。
        data:        表单请求体。
        cookies:     显式传入 Cookie；不传则自动从保存文件读取。
        cookie_mgr:  自定义 CookieManager（默认 platform=taobao）。

    返回:
        httpx.Response。若没有可用 Cookie，会抛 RuntimeError。

    示例::
        resp = await request("https://main.m.taobao.com/", method="GET")
    """
    cookies = cookies if cookies is not None else (load_cookies(cookie_mgr) or [])
    if not cookies:
        raise RuntimeError("未找到有效的淘宝 Cookie，请先运行 login_and_save() 完成扫码登录。")

    if not url.startswith("http"):
        url = f"https://main.m.taobao.com/{url.lstrip('/')}"

    cookie_str = cookie_header(cookies)
    hdrs = {
        "User-Agent": MOBILE_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://main.m.taobao.com/",
        "Cookie": cookie_str,
    }
    if headers:
        hdrs.update(headers)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0), follow_redirects=True, headers=hdrs
    ) as client:
        resp = await client.request(
            method, url, params=params, json=json_body, data=data
        )
    return resp


# ---------------------------------------------------------------------------
# 4) 登录态校验
# ---------------------------------------------------------------------------

async def check_login(cookie_mgr: Optional[CookieManager] = None) -> bool:
    """校验本地保存的登录态是否有效。

    判断依据（本地、无需网络）：
    - Cookie 文件存在且未过期（CookieManager 判定）；
    - 包含 cookie2 及用户标记 Cookie（tracknick / unb / lgc）之一。

    需要更强的网络验证时，可自行用 request() 访问一个需要登录的接口并判断响应。

    返回:
        True 表示登录态大概率有效。
    """
    cookies = load_cookies(cookie_mgr)
    if not cookies:
        return False
    return _is_logged_in(cookies)
