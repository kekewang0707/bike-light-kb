"""通用 CRUD 辅助类。

提供 CRUDBase[T] 泛型基类，封装 SQLAlchemy 的常用增删改查操作。
所有模型都可以直接用 CRUDBase(MyModel) 获得标准的 CRUD 方法，
无需为每个模型重复编写相似的数据库操作代码。

设计理念：
- 泛型约束：T 绑定到具体的 ORM 模型类，提供类型安全
- 最小惊喜：所有方法接受 Session 作为第一个参数，显式控制事务边界
- 灵活过滤：查询方法支持 **filters 关键字参数，自动转换为 WHERE 条件

典型用法::

    from models import CRUDBase, Product

    product_crud = CRUDBase(Product)

    # 查询
    p = product_crud.get(session, id=1)
    results = product_crud.list(session, brand="BrightRide", limit=20)

    # 写入
    p = product_crud.create(session, name="新商品", price=99.0)

    # 存在则更新，不存在则创建
    p = product_crud.upsert_by(
        session,
        filters={"platform": "taobao", "platform_id": "12345"},
        updates={"price": 89.0, "sales_volume": 1000},
    )

    # 删除
    product_crud.delete(session, id=1)
    deleted_count = product_crud.delete_by(session, brand="已停售品牌")
"""

from typing import Type, TypeVar, Generic, List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func

# ---------------------------------------------------------------------------
# 泛型类型变量 — T 在实例化时绑定到具体的 ORM 模型
# ---------------------------------------------------------------------------
T = TypeVar("T")


class CRUDBase(Generic[T]):
    """通用 CRUD 基类 — 提供标准的增删改查方法。

    参数:
        model: SQLAlchemy ORM 模型类（如 Product、Review 等）

    每个方法都要求显式传入 Session 对象，由调用方控制事务边界。
    方法内不自动 commit（除 create/update/delete 等写入方法外），
    调用方应在合适的时机统一 commit 或 rollback。
    """

    def __init__(self, model: Type[T]):
        """初始化 CRUD 实例。

        参数:
            model: 要操作的 ORM 模型类，例如 CRUDBase(Product)
        """
        self.model = model

    # ==================================================================
    # 查询方法
    # ==================================================================

    def get(self, session: Session, id: int) -> Optional[T]:
        """按主键 ID 获取单条记录。

        参数:
            session: 数据库会话
            id:       主键值

        返回:
            找到的模型实例，不存在则返回 None
        """
        return session.get(self.model, id)

    def get_by(self, session: Session, **filters) -> Optional[T]:
        """按条件过滤，返回第一条匹配记录。

        参数:
            session: 数据库会话
            **filters: 字段名=值 的过滤条件，如 brand="BrightRide"

        返回:
            第一条匹配的模型实例，无匹配则返回 None

        注意:
            如果有多条匹配，只返回第一条。需要全部结果请使用 list()。
        """
        return session.query(self.model).filter_by(**filters).first()

    def list(
        self,
        session: Session,
        skip: int = 0,
        limit: int = 100,
        order_by=None,
        **filters,
    ) -> List[T]:
        """分页列表查询。

        参数:
            session:  数据库会话
            skip:     跳过的记录数（偏移量），用于分页
            limit:    返回的最大记录数（默认 100，防止一次加载过多数据）
            order_by: SQLAlchemy 排序表达式，如 Product.sales_volume.desc()
            **filters: 字段名=值 的过滤条件

        返回:
            匹配的模型实例列表（可能为空列表）

        示例::

            # 查询 BrightRide 品牌按销量降序的前 20 条
            products = crud.list(
                session, brand="BrightRide",
                order_by=Product.sales_volume.desc(), limit=20,
            )
        """
        q = session.query(self.model).filter_by(**filters)
        if order_by is not None:
            q = q.order_by(order_by)
        return q.offset(skip).limit(limit).all()

    def count(self, session: Session, **filters) -> int:
        """按条件统计记录数。

        参数:
            session:  数据库会话
            **filters: 过滤条件

        返回:
            匹配条件的记录总数

        性能说明:
            使用 SQL COUNT 聚合函数，在数据库层完成计数，不会加载数据到内存。
        """
        return (
            session.query(func.count())
            .select_from(self.model)
            .filter_by(**filters)
            .scalar()
        )

    def exists(self, session: Session, **filters) -> bool:
        """检查是否存在满足条件的记录。

        参数:
            session:  数据库会话
            **filters: 过滤条件

        返回:
            True 表示至少有一条匹配记录

        性能说明:
            使用 SQL EXISTS 子查询，数据库在找到第一条匹配后立即返回，
            不会扫描全部数据，适合做去重判断。
        """
        return session.query(
            session.query(self.model).filter_by(**filters).exists()
        ).scalar()

    # ==================================================================
    # 写入方法
    # ==================================================================

    def create(self, session: Session, **kwargs) -> T:
        """创建一条新记录并立即提交。

        参数:
            session: 数据库会话
            **kwargs: 模型字段名=值，如 name="XX车灯", price=89.0

        返回:
            创建后的模型实例（已刷新，包含数据库生成的 id 等字段）

        注意:
            此方法内部调用 session.commit()，会提交当前事务中的所有未提交更改。
            如需批量创建后统一提交，请使用 bulk_create()。
        """
        obj = self.model(**kwargs)
        session.add(obj)
        session.commit()
        session.refresh(obj)            # 刷新以获取数据库生成的默认值
        return obj

    def get_or_create(
        self, session: Session, defaults: Dict[str, Any], **filters
    ) -> T:
        """按条件查找，存在则返回，不存在则创建。

        参数:
            session:  数据库会话
            defaults: 创建新记录时使用的默认值字典
            **filters: 用于查找的过滤条件

        返回:
            已存在的或新创建的模型实例

        示例::

            # 按 platform+platform_id 查找，不存在则创建
            product = crud.get_or_create(
                session,
                defaults={"name": "新商品", "price": 99.0},
                platform="taobao", platform_id="12345",
            )
        """
        obj = self.get_by(session, **filters)
        if obj:
            return obj
        # 合并查找条件和默认值作为创建参数
        return self.create(session, **{**filters, **defaults})

    def upsert_by(
        self,
        session: Session,
        filters: Dict[str, Any],
        updates: Dict[str, Any],
    ) -> T:
        """按条件查找，存在则更新字段，不存在则创建。

        参数:
            session: 数据库会话
            filters: 用于查找的过滤条件（dict 形式）
            updates: 需要更新的字段和值

        返回:
            更新后或新创建的模型实例

        示例::

            # 淘宝商品重复采集时自动更新价格和销量
            product = crud.upsert_by(
                session,
                filters={"platform": "taobao", "platform_id": "taobao-test-001"},
                updates={"price": 89.0, "sales_volume": 2500, "crawled_at": now},
            )
        """
        obj = self.get_by(session, **filters)
        if obj:
            # 存在 — 逐字段更新
            for key, value in updates.items():
                setattr(obj, key, value)
            session.commit()
            session.refresh(obj)
        else:
            # 不存在 — 合并 filters + updates 创建新记录
            obj = self.create(session, **{**filters, **updates})
        return obj

    def bulk_create(
        self, session: Session, items: List[Dict[str, Any]]
    ) -> List[T]:
        """批量插入记录（先 add_all，再一次 commit）。

        参数:
            session: 数据库会话
            items:   字典列表，每个字典对应一条记录的字段值

        返回:
            创建的模型实例列表

        性能说明:
            与逐条 create() 相比，bulk_create() 只执行一次 commit，
            在大批量写入时性能提升显著（减少数据库往返次数）。

        注意:
            此方法不会 refresh 返回的对象。如果需要访问数据库生成的字段
            （如自增 ID），请在 commit 后重新查询。
        """
        objects = [self.model(**item) for item in items]
        session.add_all(objects)
        session.commit()
        return objects

    # ==================================================================
    # 更新 & 删除
    # ==================================================================

    def update(self, session: Session, id: int, **kwargs) -> Optional[T]:
        """按主键 ID 更新记录的部分字段。

        参数:
            session: 数据库会话
            id:      主键值
            **kwargs: 要更新的字段名=新值

        返回:
            更新后的模型实例，记录不存在则返回 None

        示例::

            # 将商品价格更新为 79.0
            product = crud.update(session, id=1, price=79.0)
        """
        obj = self.get(session, id)
        if obj:
            for key, value in kwargs.items():
                setattr(obj, key, value)
            session.commit()
            session.refresh(obj)
        return obj

    def delete(self, session: Session, id: int) -> bool:
        """按主键 ID 删除单条记录。

        参数:
            session: 数据库会话
            id:      主键值

        返回:
            True 表示成功删除，False 表示记录不存在
        """
        obj = self.get(session, id)
        if obj:
            session.delete(obj)
            session.commit()
            return True
        return False

    def delete_by(self, session: Session, **filters) -> int:
        """按条件批量删除记录。

        参数:
            session:  数据库会话
            **filters: 过滤条件

        返回:
            实际删除的记录条数

        注意:
            此方法直接执行 DELETE WHERE 语句，不会加载数据到内存。
            适合大批量条件删除场景。

        示例::

            # 删除某个平台的全部商品
            deleted = crud.delete_by(session, platform="taobao")
        """
        count = session.query(self.model).filter_by(**filters).delete()
        session.commit()
        return count
