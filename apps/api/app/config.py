from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://orl:orl@127.0.0.1:5432/orl"
    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3:8b"
    embedding_model: str = "qwen3-embedding:0.6b"


def get_settings() -> Settings:
    return Settings()
