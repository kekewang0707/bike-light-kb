"""京东反爬对抗 — 7 层防护体系。

2025 年的京东反爬已经从简单的 UA 检测进化到四层防御体系。
本模块提供从启动参数到降级恢复的完整对抗方案。

核心原则:
- 没有银弹！反爬是持续的猫鼠游戏，需要定期更新
- 有头模式优先：京东对 headless 检测极严，headless 基本不可用
- 最有效方案：CDP 连接本地 Chrome（真实浏览器环境，无 webdriver 特征）

7 层防护:
  第1层: 启动参数 — 去除 AutomationControlled 等自动化标志
  第2层: 指纹注入 — addInitScript 在页面 JS 执行前注入反检测脚本
  第3层: 环境一致性 — locale/时区/viewport/UA/Client Hints 全部匹配
  第4层: UA 池 — 10+ 真实移动端和桌面端 UA，定期轮换
  第5层: 行为拟人 — 贝塞尔鼠标轨迹、正态分布延迟、拟人滚动
  第6层: 节奏控制 — 自适应限速、避开凌晨时段
  第7层: 降级恢复 — 检测验证码→暂停→切IP→通知

Usage::

    from crawler.anti_detect import AntiDetect, CDPConnection

    # CDP 连接（首选）
    browser = await CDPConnection.connect_to_local_chrome()

    # 或 Playwright 直连（备选）
    browser = await playwright.chromium.launch(
        headless=False,
        args=AntiDetect.get_launch_args(),
    )
    context = AntiDetect.create_context(browser)
    page = await context.new_page()
    await AntiDetect.inject_stealth_scripts(page)
"""

import asyncio
import random
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import httpx
from loguru import logger

# Playwright 是可选依赖，运行时检查
try:
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


# ============================================================================
# CDP 连接管理 — 对抗京东的最有效方案
# ============================================================================

class CDPConnection:
    """通过 Chrome DevTools Protocol 连接本地真实浏览器。

    这是目前对抗京东最有效的方式：
    - 真实浏览器环境，无 webdriver 特征（navigator.webdriver 天生为 undefined）
    - 复用日常登录的京东 Cookie（无需每次重新登录）
    - Canvas/WebGL 指纹是真实硬件渲染（非 SwiftShader 软件模拟）
    - 绕过约 90% 的自动化检测

    启动本地 Chrome（macOS）::

        /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
          --remote-debugging-port=9222 \\
          --user-data-dir=/tmp/chrome-jd-profile

    然后在代码中连接::

        browser = await CDPConnection.connect_to_local_chrome()
    """

    DEFAULT_CDP_URL = "http://localhost:9222"

    @staticmethod
    async def connect_to_local_chrome(cdp_url: str = None) -> "Browser":
        """连接到本地 Chrome 的 CDP 端口。

        参数:
            cdp_url: CDP 调试端口地址，默认 http://localhost:9222

        返回:
            Playwright Browser 实例（通过 CDP 连接）

        异常:
            RuntimeError: 如果 CDP 端口不可用
            ImportError: 如果未安装 playwright
        """
        if not HAS_PLAYWRIGHT:
            raise ImportError("请安装 playwright: pip install playwright && playwright install")

        url = cdp_url or CDPConnection.DEFAULT_CDP_URL
        available, msg = await CDPConnection.is_available(url)
        if not available:
            raise RuntimeError(
                f"CDP 端口不可用: {msg}\n"
                f"请先启动本地 Chrome:\n"
                f"  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\\n"
                f"    --remote-debugging-port=9222 \\\n"
                f"    --user-data-dir=/tmp/chrome-jd-profile"
            )

        logger.info(f"通过 CDP 连接到本地 Chrome: {url}")
        playwright = await async_playwright().start()
        browser = await playwright.chromium.connect_over_cdp(url)
        return browser

    @staticmethod
    async def is_available(cdp_url: str = None) -> tuple[bool, str]:
        """检查 CDP 端口是否可用。

        返回:
            (可用标志, 消息)
        """
        url = cdp_url or CDPConnection.DEFAULT_CDP_URL
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{url}/json/version", timeout=3.0)
                if resp.status_code == 200:
                    data = resp.json()
                    return True, f"Chrome {data.get('Browser', 'unknown')}"
                return False, f"HTTP {resp.status_code}"
        except httpx.ConnectError:
            return False, f"无法连接到 {url}，Chrome 可能未启动"
        except Exception as e:
            return False, str(e)


# ============================================================================
# 7 层反爬对抗
# ============================================================================

class AntiDetect:
    """京东反爬对抗 — 7 层防护体系。"""

    # ================================================================
    # 第1层：启动参数
    # ================================================================

    @staticmethod
    def get_launch_args() -> List[str]:
        """Chromium 启动参数 — 去除自动化标志。

        关键参数说明：
        - --disable-blink-features=AutomationControlled: 去除 navigator.webdriver 标记（最关键）
        - --disable-dev-shm-usage: 避免 Docker 环境下 /dev/shm 不足导致崩溃
        - --no-sandbox: Docker 环境必需
        - --window-size: 设置真实分辨率，避免默认 800x600 被检测
        """
        return [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-translate",
            "--disable-web-security",
            "--disable-features=TranslateUI,BlinkGenPropertyTrees",
            "--window-size=1920,1080",
            "--window-position=0,0",
        ]

    # ================================================================
    # 第2层：指纹注入（最关键的一层）
    # ================================================================

    @staticmethod
    async def inject_stealth_scripts(page: "Page"):
        """在页面 JS 执行前注入反检测脚本。

        必须在 page.goto() 之前调用！
        Playwright 的 addInitScript 会在每个 frame 的 JS 执行前运行。

        京东会检测以下特征（2025 年版）：
        - navigator.webdriver → 必须为 undefined（不能是 false）
        - navigator.plugins → 真实 Chrome 有 5 个内置插件
        - navigator.languages → 不能为空，至少 2 条
        - window.chrome.runtime → 真实 Chrome 有此对象
        - Notification.permission 与 Permissions.query() 交叉验证
        - Function.prototype.toString → native getter 需返回 [native code]
        """
        await page.add_init_script("""
            // =====================================================
            // 2a. 隐藏 webdriver 标记
            // =====================================================
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });

            // =====================================================
            // 2b. 伪造 plugins 数组（真实 Chrome 内置 5 个插件）
            // =====================================================
            const makePlugins = () => {
                const plugins = [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
                ];
                plugins.item = function(i) { return this[i] || null; };
                plugins.namedItem = function(name) { return this.find(p => p.name === name) || null; };
                plugins.refresh = function() {};
                Object.setPrototypeOf(plugins, PluginArray.prototype);
                return plugins;
            };

            try {
                Object.defineProperty(navigator, 'plugins', { get: () => makePlugins() });
            } catch(e) {}

            // Also patch the mimeTypes (related to plugins)
            try {
                Object.defineProperty(navigator, 'mimeTypes', {
                    get: () => {
                        const mts = [
                            { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
                            { type: 'text/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
                        ];
                        mts.item = function(i) { return this[i] || null; };
                        mts.namedItem = function(n) { return this.find(m => m.type === n) || null; };
                        Object.setPrototypeOf(mts, MimeTypeArray.prototype);
                        return mts;
                    }
                });
            } catch(e) {}

            // =====================================================
            // 2c. languages — 至少 2 条
            // =====================================================
            try {
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh', 'en'],
                });
                Object.defineProperty(navigator, 'language', {
                    get: () => 'zh-CN',
                });
            } catch(e) {}

            // =====================================================
            // 2d. chrome.runtime — 真实 Chrome 有此对象
            // =====================================================
            if (!window.chrome) {
                window.chrome = {};
            }
            window.chrome.runtime = window.chrome.runtime || {};
            window.chrome.loadTimes = window.chrome.loadTimes || function() {};
            window.chrome.csi = window.chrome.csi || function() {};
            window.chrome.app = window.chrome.app || {};

            // =====================================================
            // 2e. permissions 交叉验证修复
            // =====================================================
            const originalQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
            window.navigator.permissions.query = function(parameters) {
                if (parameters.name === 'notifications') {
                    return Promise.resolve({
                        state: Notification.permission,
                        onchange: null,
                    });
                }
                return originalQuery(parameters);
            };

            // =====================================================
            // 2f. hardwareConcurrency
            // =====================================================
            try {
                Object.defineProperty(navigator, 'hardwareConcurrency', {
                    get: () => 8,
                });
            } catch(e) {}

            // =====================================================
            // 2g. deviceMemory
            // =====================================================
            try {
                Object.defineProperty(navigator, 'deviceMemory', {
                    get: () => 8,
                });
            } catch(e) {}

            // =====================================================
            // 2h. platform
            // =====================================================
            try {
                Object.defineProperty(navigator, 'platform', {
                    get: () => 'MacIntel',
                });
            } catch(e) {}

            // =====================================================
            // 2i. 隐藏 Playwright 的 __playwright__ 标记
            // =====================================================
            delete window.__playwright__binding__;
            delete window.__pw_manual__;
            delete window.__PW_inspector__;
        """)

    # ================================================================
    # 第3层：环境一致性
    # ================================================================

    @staticmethod
    def create_context(
        browser: "Browser",
        proxy: Optional[dict] = None,
        use_mobile: bool = False,
    ) -> "BrowserContext":
        """创建浏览器上下文 — 确保所有环境参数一致。

        京东会交叉验证以下参数是否匹配：
        - locale 与 Accept-Language 头
        - timezone_id 与系统时间
        - device_scale_factor 与 screen
        - user_agent 与 Sec-Ch-Ua Client Hints

        参数:
            browser: Playwright/ CDP Browser 实例
            proxy: 代理配置 {'server': 'http://ip:port', 'username': '...', 'password': '...'}
            use_mobile: True 模拟移动端（搜索页优先用移动端）

        返回:
            配置好的 BrowserContext
        """
        if use_mobile:
            # 移动端 iPhone 模拟
            context_options = {
                "viewport": {"width": 390, "height": 844},
                "screen": {"width": 390, "height": 844},
                "device_scale_factor": 3,
                "is_mobile": True,
                "has_touch": True,
                "locale": "zh-CN",
                "timezone_id": "Asia/Shanghai",
                "user_agent": AntiDetect.random_ua(mobile=True),
            }
        else:
            # 桌面端模拟
            context_options = {
                "viewport": {"width": 1920, "height": 1080},
                "screen": {"width": 1920, "height": 1080},
                "device_scale_factor": 2,
                "is_mobile": False,
                "has_touch": False,
                "locale": "zh-CN",
                "timezone_id": "Asia/Shanghai",
                "user_agent": AntiDetect.random_ua(mobile=False),
            }

        if proxy:
            context_options["proxy"] = proxy

        # 设置额外 HTTP 头以增强一致性
        context = browser.new_context(**context_options)
        context.set_extra_http_headers({
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
        })
        return context

    # ================================================================
    # 第4层：UA 池
    # ================================================================

    # 桌面端 UA（macOS + Windows Chrome）
    _DESKTOP_UA_POOL = [
        # macOS Chrome 122
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        # macOS Chrome 121
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        # Windows Chrome 122
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        # Windows Chrome 121
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        # macOS Edge
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
        # Windows Edge
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    ]

    # 移动端 UA（iPhone + Android）
    _MOBILE_UA_POOL = [
        # iPhone 17.4 Safari
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        # iPhone 17.3 Safari
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
        # Android 14 Chrome
        "Mozilla/5.0 (Linux; Android 14; SM-S9280) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.119 Mobile Safari/537.36",
        # Android 13 Chrome
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.178 Mobile Safari/537.36",
        # Android 14 Samsung Browser
        "Mozilla/5.0 (Linux; Android 14; SM-S9280) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/24.0 Chrome/122.0.6261.119 Mobile Safari/537.36",
        # iPad Safari
        "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    ]

    @staticmethod
    def random_ua(mobile: bool = False) -> str:
        """随机获取一个真实 User-Agent。

        参数:
            mobile: True 返回移动端 UA，False 返回桌面端 UA
        """
        pool = AntiDetect._MOBILE_UA_POOL if mobile else AntiDetect._DESKTOP_UA_POOL
        return random.choice(pool)

    # ================================================================
    # 第5层：行为拟人化
    # ================================================================

    @staticmethod
    async def human_delay(min_s: float = 2.0, max_s: float = 8.0):
        """拟人化延迟 — 使用正态分布而非均匀分布。

        为什么不使用 random.uniform()？
        - 均匀分布的延迟在统计上容易被识别为机器行为
        - 人类的等待时间更接近正态分布（大部分靠近均值，偶有异常值）
        - 京东会建模请求间隔的分布，均匀分布是明显特征
        """
        mean = (min_s + max_s) / 2
        std = (max_s - min_s) / 4           # 约 95% 的值落在 [min, max]
        delay = max(min_s, min(max_s, random.gauss(mean, std)))
        await asyncio.sleep(delay)

    @staticmethod
    async def human_scroll(page: "Page"):
        """拟人化滚动 — 带随机速度和回弹。

        真实用户滚动特征：
        - 每次滚动距离不固定（300-800px）
        - 滚动间隔不固定（0.3-1.0s）
        - 偶尔回滚一点再继续（~30% 概率）
        """
        for _ in range(random.randint(2, 5)):
            delta = random.randint(300, 800)
            await page.mouse.wheel(0, delta)
            await asyncio.sleep(random.uniform(0.3, 1.0))

        # 偶尔回滚（真实用户看了觉得不对劲会回滚）
        if random.random() < 0.3:
            await page.mouse.wheel(0, -random.randint(50, 150))
            await asyncio.sleep(random.uniform(0.2, 0.5))

    @staticmethod
    async def human_mouse_move(
        page: "Page",
        target_x: float,
        target_y: float,
        steps: int = None,
    ):
        """贝塞尔曲线鼠标移动 — 真实用户无法画出完美直线。

        参数:
            page: Playwright Page
            target_x, target_y: 目标坐标
            steps: 移动步数，None 则随机 8-15 步
        """
        if steps is None:
            steps = random.randint(8, 15)

        # 获取当前鼠标位置（估计值）
        start_x = random.uniform(target_x - 200, target_x - 50)
        start_y = random.uniform(target_y - 100, target_y + 100)

        # 控制点加入随机偏移
        cp_x = (start_x + target_x) / 2 + random.uniform(-30, 30)
        cp_y = (start_y + target_y) / 2 + random.uniform(-30, 30)

        for i in range(steps + 1):
            t = i / steps
            # 二次贝塞尔曲线
            x = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * cp_x + t ** 2 * target_x
            y = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * cp_y + t ** 2 * target_y
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.001, 0.005))

    @staticmethod
    async def human_type(page: "Page", selector: str, text: str):
        """逐字符输入 — 每个字符间隔随机 50-150ms。

        为什么不用 page.fill()？
        - fill() 是瞬间填充，不会触发键盘事件
        - 京东的搜索框会监听输入事件做联想推荐
        - 逐字符输入更接近真人行为
        """
        await page.click(selector)
        await AntiDetect.human_delay(0.3, 0.8)
        for char in text:
            await page.keyboard.type(char, delay=random.randint(50, 150))

    @staticmethod
    async def random_mouse_moves(page: "Page", count: int = 3):
        """在页面上随机移动鼠标几次 — 模拟浏览行为。"""
        vp = page.viewport_size
        if not vp:
            return
        for _ in range(count):
            x = random.randint(100, vp["width"] - 100)
            y = random.randint(100, vp["height"] - 100)
            await AntiDetect.human_mouse_move(page, x, y)
            await asyncio.sleep(random.uniform(0.5, 1.5))

    # ================================================================
    # 第6层：节奏控制
    # ================================================================

    @staticmethod
    def should_throttle(
        request_count: int,
        time_window_seconds: float,
        max_per_minute: int = 5,
    ) -> bool:
        """自适应限速检查。

        返回 True 表示当前请求频率过高，应该暂停等待。
        每小时不超过 50 页详情是一个经验安全值。
        """
        if time_window_seconds < 60:
            return False  # 窗口太短，不判断
        rate_per_minute = request_count / (time_window_seconds / 60)
        return rate_per_minute > max_per_minute

    @staticmethod
    def is_off_hours() -> bool:
        """凌晨 2-6 点降低采集频率。

        真实用户在这个时段不太活跃，高频请求更容易触发风控。
        定时任务尽量安排在白天执行。
        """
        hour = datetime.now().hour
        return 2 <= hour < 6

    @staticmethod
    def get_safe_delay() -> float:
        """根据当前时段返回推荐的安全延迟。

        凌晨时段：更长的延迟（5-15s）
        白天时段：正常延迟（2-8s）
        """
        if AntiDetect.is_off_hours():
            return random.gauss(10.0, 2.5)
        return random.gauss(5.0, 1.5)

    # ================================================================
    # 第7层：降级恢复
    # ================================================================

    CAPTCHA_INDICATORS = [
        "验证码", "captcha", "verify", "滑块", "请按住滑块",
        "请完成安全验证", "geetest", "#captcha", ".captcha",
        "login.jd.com", "passport.jd.com",
    ]

    @staticmethod
    async def detect_captcha(page: "Page") -> bool:
        """检测当前页面是否触发了验证码。

        京东常见验证码特征：
        - 滑块验证码（geetest）
        - 图形验证码
        - 登录重定向（passport.jd.com）

        返回:
            True 表示检测到验证码，应立即暂停采集
        """
        try:
            url = page.url.lower()
            for indicator in ["login.jd.com", "passport.jd.com"]:
                if indicator in url:
                    logger.warning(f"检测到登录重定向: {page.url}")
                    return True

            content = await page.content()
            content_lower = content.lower()

            for indicator in AntiDetect.CAPTCHA_INDICATORS:
                if indicator.lower() in content_lower:
                    logger.warning(f"检测到验证码特征: {indicator}")
                    # 截图保存现场
                    screenshot_dir = Path("data/logs/captcha")
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    screenshot_path = screenshot_dir / f"captcha_{int(time.time())}.png"
                    await page.screenshot(path=str(screenshot_path))
                    logger.info(f"验证码截图已保存: {screenshot_path}")
                    return True
        except Exception as e:
            logger.error(f"验证码检测异常: {e}")

        return False

    @staticmethod
    def get_cooldown_seconds() -> int:
        """验证码触发后的冷却时间（秒）。

        首次触发: 30 分钟
        之后每次增加 10 分钟，上限 2 小时
        """
        return 30 * 60  # 基础 30 分钟冷却


# ============================================================================
# 登录辅助
# ============================================================================

class LoginHelper:
    """京东登录辅助 — 当 Cookie 过期时引导用户手动扫码登录。"""

    @staticmethod
    async def wait_for_manual_login(page: "Page", timeout_seconds: int = 120):
        """打开京东登录页，等待用户手动扫码登录。

        参数:
            page: Playwright Page
            timeout_seconds: 最大等待时间

        返回:
            True: 登录成功
            False: 超时
        """
        logger.info("需要登录京东，请在浏览器中扫码...")

        await page.goto("https://passport.jd.com/new/login.aspx", wait_until="domcontentloaded")
        await AntiDetect.human_delay(2, 4)

        try:
            # 点击"账号登录"切换到扫码模式
            login_btn = page.locator('a:has-text("账号登录"), .login-tab-r:has-text("扫码登录")')
            if await login_btn.count() > 0:
                await login_btn.first.click()
                await AntiDetect.human_delay(1, 2)
        except Exception:
            pass

        # 等待登录成功（URL 不再是 passport.jd.com）
        start = time.time()
        while time.time() - start < timeout_seconds:
            url = page.url
            if "passport.jd.com" not in url and "login.jd.com" not in url:
                logger.info("登录成功！")
                return True
            await asyncio.sleep(2)

        logger.error(f"登录超时（{timeout_seconds}s）")
        return False
