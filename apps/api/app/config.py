"""全局配置：pydantic-settings 从环境变量/.env 读取，默认值面向本地开发。

面试考点：jwt_secret 的默认值仅用于开发，线上必须用环境变量覆盖；
embedding_dim 必须与 embedding 模型的实际维度一致（llm.embed 会校验）。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict

EMBEDDING_DIM = 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://orl:orl@127.0.0.1:5432/orl"
    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3.5:4b"
    embedding_model: str = "qwen3-embedding:0.6b"
    embedding_dim: int = EMBEDDING_DIM
    redis_url: str | None = None  # None = 降级跑（限流/广播失效，主流程可用）
    chat_rate_limit: int = 20
    chat_rate_window_seconds: int = 60


def get_settings() -> Settings:
    return Settings()
