"""应用配置"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # PostgreSQL
    db_host: str = "localhost"
    db_port: int = 5433
    db_name: str = "bikelight_kb"
    db_user: str = "bikelight"
    db_password: str = "bikelight123"

    # ChromaDB
    chroma_host: str = "localhost"
    chroma_port: int = 8000

    # LLM / DeepSeek
    deepseek_api_key: str = ""

    # 多模态 VL (Qwen-VL via DashScope)
    dashscope_api_key: str = ""

    class Config:
        env_prefix = "BKL_"
        env_file = ".env"
        # 容忍 .env 里未被定义的 BKL_* 变量（如旧的 crawler 配置），避免实例化失败
        extra = "allow"


settings = Settings()
