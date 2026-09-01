"""反爬对抗 — 启动参数、指纹注入、环境一致性、UA 池、行为拟人。

为 Playwright 浏览器提供反自动化检测能力：
- get_launch_args: 去除 AutomationControlled 等自动化标志
- inject_stealth_scripts: 在页面 JS 执行前注入反检测脚本
- create_context: 统一 locale/时区/viewport/UA 等环境参数
- random_ua: 10+ 真实移动端/桌面端 UA 池
- human_delay / human_scroll: 拟人化延迟与滚动
"""

import asyncio
import random
from typing import List, Optional


# ============================================================================
# 反爬对抗
# ============================================================================

class AntiDetect:
    """反爬对抗 — 启动参数、指纹注入、环境一致性、UA 池、行为拟人。"""

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
    async def create_context(
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

        # Playwright 1.45+: HTTP headers 作为 new_context 参数传入
        context_options["extra_http_headers"] = {
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
        }
        context = await browser.new_context(**context_options)
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
