"""自行车灯电商知识库 — 数据模型层统一入口。

本模块是数据模型层的唯一导入入口，提供：
- 所有 ORM 模型类（Product、Review 等 9 个模型）
- 数据库基础设施（Base、engine、SessionLocal）
- 便捷函数（get_session、init_db）
- CRUD 工具类（CRUDBase）

使用示例::

    from models import init_db, get_session, Product, CRUDBase

    # 1. 自动建表（首次运行时）
    init_db()

    # 2. 创建会话
    session = next(get_session())

    # 3. 操作数据
    product = Product(name="XX车灯", platform="taobao", platform_id="123", price=89.0)
    session.add(product)
    session.commit()

    # 4. 使用 CRUD 工具
    crud = CRUDBase(Product)
    products = crud.list(session, brand="BrightRide", limit=20)

    session.close()

模型关系图::

    Product 1──N ProductImage ──1──1 ImageAnalysis
    Product 1──N ProductSKU
    Product 1──N Review ──1──N ReviewImage
             │            └──1──1 ReviewAnalysis
             ├──N PriceHistory
             └──N HotRanking
"""

from models.base import Base, engine, SessionLocal, get_session, init_db
from models.product import Product, ProductImage, ProductSKU
from models.review import Review, ReviewImage
from models.price_history import PriceHistory
from models.ranking import HotRanking
from models.analysis import ReviewAnalysis, ImageAnalysis
from models.crud import CRUDBase

__all__ = [
    # ---- 基础设施 ----
    "Base",             # ORM 声明基类
    "engine",           # 数据库引擎（连接池）
    "SessionLocal",     # 会话工厂
    "get_session",      # 获取会话的生成器函数
    "init_db",          # 自动建表函数
    # ---- 模型（9 个表） ----
    "Product",          # 商品主表
    "ProductImage",     # 商品图片
    "ProductSKU",       # 商品 SKU 规格
    "Review",           # 评价主表
    "ReviewImage",      # 评价晒图
    "PriceHistory",     # 价格变动记录
    "HotRanking",       # 热榜快照
    "ReviewAnalysis",   # 评价语义分析结果
    "ImageAnalysis",    # 图片分析结果
    # ---- 工具 ----
    "CRUDBase",         # 通用 CRUD 基类
]
