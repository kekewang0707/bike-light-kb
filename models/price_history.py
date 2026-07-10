"""价格历史 ORM 模型。

PriceHistory (价格变动记录)
    └── 每日对每个活跃商品记录一次价格快照。
        通过积累每日价格数据，可以：
        - 绘制商品价格历史走势图（选品看板的趋势分析模块）
        - 检测异常价格波动（单日涨跌 > 30% 触发告警）
        - 分析竞品的价格策略（降价周期、大促规律）

关系链::

    Product 1──N PriceHistory
"""

from sqlalchemy import Column, BigInteger, Date, ForeignKey, Numeric
from sqlalchemy.orm import relationship

from models.base import Base


class PriceHistory(Base):
    """价格变动记录 — 每日价格快照。

    设计思路：
    - snapshot_date 存日期（不含时分秒），每个商品每天至多一条记录
    - price 使用 DECIMAL(10,2) 精确存储，避免浮点运算误差
    - 定时任务每天凌晨 3 点对活跃商品执行价格快照
    - 通过 (product_id, snapshot_date) 组合可建唯一索引防止重复快照
    """

    __tablename__ = "price_history"

    # ---- 主键 ----
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # ---- 外键 ----
    product_id = Column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False,
        comment="关联商品ID",
    )

    # ---- 价格数据 ----
    price = Column(
        Numeric(10, 2), nullable=False,
        comment="当日售价（元），精确到分",
    )
    snapshot_date = Column(
        Date, nullable=False,
        comment="价格快照日期（不含时分秒）",
    )

    # ==================================================================
    # 关系映射
    # ==================================================================
    product = relationship(
        "Product", back_populates="price_history",
        doc="关联商品（多对一）",
    )

    __table_args__ = ({"comment": "价格变动记录表"},)

    def __repr__(self):
        return (
            f"<PriceHistory(id={self.id}, product_id={self.product_id}, "
            f"price={self.price}, date={self.snapshot_date})>"
        )
