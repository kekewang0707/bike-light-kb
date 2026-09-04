"""SQLAlchemy 数据库引擎、会话工厂、声明基类。

本模块是整个数据模型层的基石，负责：
- 创建数据库连接引擎（连接池管理）
- 提供线程安全的会话工厂
- 导出 ORM 声明基类（所有模型继承自此基类）
- 提供便捷的 get_session() 和 init_db() 函数

典型用法::

    from models.base import Base, engine, get_session, init_db

    # 定义模型（继承 Base）
    class MyModel(Base):
        __tablename__ = "my_table"
        ...

    # 自动建表
    init_db()

    # 获取会话
    session = next(get_session())
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

from config.settings import settings
from loguru import logger

# ---------------------------------------------------------------------------
# 数据库连接 URL
# ---------------------------------------------------------------------------
# 格式: postgresql://用户名:密码@主机:端口/数据库名
# 配置值来自 config/settings.py，可通过环境变量 BKL_DB_* 覆盖
DATABASE_URL = (
    f"postgresql://{settings.db_user}:{settings.db_password.get_secret_value()}"
    f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
)

# ---------------------------------------------------------------------------
# 数据库引擎
# ---------------------------------------------------------------------------
# pool_size:     连接池常驻连接数（默认 10）
# max_overflow:  超出 pool_size 时最多额外创建的连接数（默认 20）
# pool_pre_ping: 每次从池中取出连接前先发一个 SELECT 1 探测有效性，
#                防止使用已被数据库端关闭的连接（对长期空闲连接很重要）
# echo:          True 时打印所有 SQL 语句，调试用；生产环境务必设为 False
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False,
)

# ---------------------------------------------------------------------------
# 会话工厂
# ---------------------------------------------------------------------------
# autocommit=False: 禁止自动提交，所有写操作需显式调用 session.commit()
# autoflush=False:  禁止自动 flush，避免查询前意外将脏数据刷入数据库
#                    需要手动控制 flush 时机，适合批量写入场景
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)

# ---------------------------------------------------------------------------
# 声明基类 — 所有 ORM 模型继承自此
# ---------------------------------------------------------------------------
Base = declarative_base()


def get_session():
    """获取数据库会话的生成器函数。

    设计为生成器的原因：
    1. 支持依赖注入模式（FastAPI 等框架的标准写法）
    2. 利用 finally 块确保会话一定被关闭，防止连接泄漏
    3. 调用方通过 next() 获取会话，通过 session.close() 归还连接

    使用示例::

        session = next(get_session())
        try:
            product = session.get(Product, 1)
            product.price = 99.0
            session.commit()        # 显式提交
        except Exception:
            session.rollback()      # 异常时回滚
            raise
        finally:
            session.close()         # 归还连接到池
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db():
    """根据所有已导入的 ORM 模型自动创建数据库表。

    原理：遍历 Base.metadata 中注册的所有表，对数据库执行 CREATE TABLE IF NOT EXISTS。

    注意事项：
    - 调用前必须确保所有 model 模块已被 import（否则对应表不会被创建）
    - 仅创建不存在的表，已有表不受影响（不会 ALTER TABLE）
    - 适用于开发/测试环境的快速建表，生产环境建议用 Alembic 管理迁移

    使用示例::

        import models.product     # 必须先导入，让模型注册到 Base.metadata
        import models.review
        from models.base import init_db
        init_db()
    """
    Base.metadata.create_all(bind=engine)
    # 兼容已有库：补齐新增列（create_all 不会 ALTER 已存在的表）
    try:
        with engine.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(
                text("ALTER TABLE products ADD COLUMN IF NOT EXISTS marketing JSONB")
            )
            conn.execute(
                text(
                    "ALTER TABLE reviews ADD COLUMN IF NOT EXISTS "
                    "user_name_hash VARCHAR(64)"
                )
            )
    except Exception as e:  # noqa: BLE001 - 列已存在等情况可忽略
        logger.warning(f"补齐 products.marketing 列失败（若列已存在可忽略）: {e}")
