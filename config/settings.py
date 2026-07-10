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

    # Crawler
    crawler_headless: bool = False
    crawler_max_products: int = 200
    crawler_request_delay: float = 2.0

    class Config:
        env_prefix = "BKL_"
        env_file = ".env"


settings = Settings()
