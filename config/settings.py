"""应用配置"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # PostgreSQL
    db_host: str = "localhost"
    db_port: int = 5433
    db_name: str = "bikelight_kb"
    db_user: str = "bikelight"
    db_password: SecretStr = SecretStr("")   # 不再硬编码；必须由环境变量 BKL_DB_PASSWORD 提供

    # ChromaDB
    chroma_host: str = "localhost"
    chroma_port: int = 8000

    # LLM / DeepSeek
    deepseek_api_key: SecretStr = SecretStr("")

    # 多模态 VL (Qwen-VL via DashScope)
    dashscope_api_key: SecretStr = SecretStr("")

    # 隐私 / 合规
    pii_pepper: SecretStr = SecretStr("bikelight-kb-pii-v1")  # 用户昵称哈希盐(pepper)，生产环境建议设为随机值
    data_retention_days: int = 365                            # 评价 PII 保留期(天)，超期对 user_name_hash 做匿名化

    # 爬虫限速 / robots 遵从（合规要求：不得对目标站点造成压力）
    crawler_rate_per_minute: float = 20.0   # 全局请求速率上限（请求/分钟）
    crawler_respect_robots: bool = True     # 是否遵从目标站点 robots.txt

    class Config:
        env_prefix = "BKL_"
        env_file = ".env"
        # 容忍 .env 里未被定义的 BKL_* 变量（如旧的 crawler 配置），避免实例化失败
        extra = "allow"


settings = Settings()

# 安全：禁止空口令（原硬编码默认值 bikelight123 已移除）
if not settings.db_password.get_secret_value():
    raise RuntimeError(
        "数据库口令未配置：请在 .env 中设置 BKL_DB_PASSWORD，"
        "且不要留空（旧默认值 bikelight123 已被移除）。"
    )
