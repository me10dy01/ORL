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
    redis_url: str | None = None
    chat_rate_limit: int = 20
    chat_rate_window_seconds: int = 60


def get_settings() -> Settings:
    return Settings()
