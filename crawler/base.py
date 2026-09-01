"""爬虫抽象基类 — 定义所有平台爬虫的接口契约。

每个平台的爬虫实现必须继承 BaseSpider 并实现全部抽象方法。
这种设计使得后续添加淘宝、拼多多等新平台只需新增一个子类，
无需修改 CrawlerEngine 和 DataPipeline。

Usage::

    from crawler.base import BaseSpider
    from crawler.schemas import ProductBrief, ProductDetail, ReviewData, PricePoint

    class MySpider(BaseSpider):
        platform = "taobao"

        async def search(self, keyword, sort_by="sales", max_items=100):
            ...
"""

from abc import ABC, abstractmethod
from typing import List

from crawler.schemas import ProductBrief, ProductDetail, ReviewData


class BaseSpider(ABC):
    """爬虫抽象基类 — 定义平台无关的爬虫接口。

    子类必须实现的 4 个方法：
    - search():        搜索商品列表
    - get_detail():     获取商品详情（规格、SKU、详情图）
    - get_reviews():    获取评价列表
    - get_price_history(): 获取历史价格
    """

    # 子类必须覆盖此属性
    platform: str = "unknown"

    # ==================================================================
    # 抽象方法
    # ==================================================================

    @abstractmethod
    async def search(
        self,
        keyword: str,
        sort_by: str = "sales",
        max_items: int = 100,
    ) -> List[ProductBrief]:
        """搜索商品列表 — 返回商品简要信息。

        参数:
            keyword:   搜索关键词，如 "自行车灯"
            sort_by:   排序方式 — sales(销量) / price(价格) / default(默认)
            max_items: 最大返回商品数

        返回:
            ProductBrief 列表

        实现要求:
        - 优先走 API 路径（200ms），失败时降级到 Playwright（3-4s）
        - 如果返回空列表，记录警告日志但不抛异常
        """
        ...

    @abstractmethod
    async def get_detail(self, product: ProductBrief) -> ProductDetail:
        """获取商品详情 — 规格参数、SKU、详情图。

        参数:
            product: 搜索阶段返回的 ProductBrief

        返回:
            ProductDetail（规格可能不完整，JSONB 允许部分空字段）

        实现要求:
        - 使用 Playwright 渲染详情页（规格区域通常需要 JS）
        - 如果某个字段抓取失败，不阻塞其他字段
        """
        ...

    @abstractmethod
    async def get_reviews(
        self,
        product: ProductBrief,
        max_pages: int = 5,
    ) -> List[ReviewData]:
        """获取商品评价列表。

        参数:
            product:   商品简要信息
            max_pages: 最多抓取多少页评价（每页约 10 条）

        返回:
            ReviewData 列表

        实现要求:
        - 优先使用 Playwright XHR 拦截方式（绕过 API 签名限制）
        - 降级方案：开放平台 API / HTML 解析
        """
        ...

    # ==================================================================
    # 可选钩子方法
    # ==================================================================

    async def setup(self):
        """爬虫初始化 — 在首次使用前调用。

        可用于: 加载 Cookie、初始化 Playwright、验证代理可用性。
        默认实现为空操作。
        """

    async def teardown(self):
        """爬虫清理 — 在所有采集完成后调用。

        可用于: 关闭浏览器、释放连接池。
        默认实现为空操作。
        """

    def __repr__(self):
        return f"<{self.__class__.__name__}(platform={self.platform})>"
