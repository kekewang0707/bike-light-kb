"""热榜 ORM 模型。

HotRanking (热榜快照)
    └── 记录商品在电商平台各类榜单中的排名快照。
        每次爬虫执行时抓取榜单数据，形成排名时间序列。

榜单类型示例：
- 自行车灯热销榜（按销量排序）
- 自行车灯好评榜（按好评率排序）
- 自行车灯新品榜（按上线时间排序）

周期类型：
- daily:  日榜（每日更新）
- weekly: 周榜（每周更新）

关系链::

    Product 1──N HotRanking
"""

from sqlalchemy import Column, BigInteger, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from models.base import Base


class HotRanking(Base):
    """热榜快照 — 记录商品在平台榜单中的排名。

    核心用途：
    - 趋势分析：对比前后两次快照，识别排名上升/下降的商品
    - 新晋爆品发现：找出首次进入榜单且增速 > 30% 的商品
    - 品牌集中度：统计各品牌在榜单中的占比（CR4 / HHI 指数）
    - 品类竞争格局：观察榜单结构变化（是否有新品牌突围）
    """

    __tablename__ = "hot_rankings"

    # ---- 主键 ----
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # ---- 外键 ----
    product_id = Column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False,
        comment="上榜商品ID",
    )

    # ---- 排名数据 ----
    ranking = Column(
        Integer,
        comment="排名位置，1 表示榜首",
    )
    list_name = Column(
        String(200),
        comment='榜单名称，如 "自行车灯热销榜"、"自行车灯好评榜"',
    )
    period_type = Column(
        String(50),
        comment="榜单周期: daily(日榜) / weekly(周榜) / monthly(月榜)",
    )

    # ---- 采集元数据 ----
    fetched_at = Column(
        DateTime(timezone=True),
        comment="榜单数据抓取时间",
    )

    # ==================================================================
    # 关系映射
    # ==================================================================
    product = relationship(
        "Product", back_populates="rankings",
        doc="上榜商品（多对一）",
    )

    __table_args__ = ({"comment": "热榜快照表"},)

    def __repr__(self):
        return (
            f"<HotRanking(id={self.id}, list={self.list_name!r}, "
            f"rank={self.ranking}, product_id={self.product_id})>"
        )
