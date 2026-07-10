"""淘宝平台爬虫 — Phase 4 实现。

淘宝/天猫的爬虫结构遵循 BaseSpider 接口，在京东爬虫稳定运行后再实现。
当前为 stub 占位。

淘宝爬虫特有挑战：
- 需要手机号登录（比京东扫码更复杂）
- 搜索页大量懒加载（需 Playwright 模拟滚动）
- 详情页评价需要登录后才能查看
- 反爬使用阿里云盾（更激进的行为建模和验证码）
"""

from crawler.base import BaseSpider
from crawler.schemas import ProductBrief, ProductDetail, ReviewData, PricePoint
from typing import List


class TaobaoSpider(BaseSpider):
    """淘宝平台爬虫 — Phase 4 实现。"""

    platform = "taobao"

    async def search(
        self, keyword: str, sort_by: str = "sales", max_items: int = 100
    ) -> List[ProductBrief]:
        raise NotImplementedError("淘宝爬虫将在 Phase 4 实现")

    async def get_detail(self, product: ProductBrief) -> ProductDetail:
        raise NotImplementedError("淘宝爬虫将在 Phase 4 实现")

    async def get_reviews(
        self, product: ProductBrief, max_pages: int = 5
    ) -> List[ReviewData]:
        raise NotImplementedError("淘宝爬虫将在 Phase 4 实现")

    async def get_price_history(self, product: ProductBrief) -> List[PricePoint]:
        raise NotImplementedError("淘宝爬虫将在 Phase 4 实现")
