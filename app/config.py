"""集中配置管理。

默认值定义在本文件；可用环境变量或 .env 覆盖（见 .env.example）。
业务代码只通过 get_settings() 读取，禁止硬编码端口、地址、密钥等。
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """应用全局配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用
    app_name: str = "星辰数码商城智能客服"
    log_level: str = "INFO"

    # MySQL
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_db: str = "orl_dev"

    # Ollama / RAG
    ollama_base_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "bge-m3"
    llm_model: str = "qwen3:8b"
    # 上下文窗口：Ollama 对 qwen3 默认 32768，KV cache 约 4.8GB，
    # 会让 8GB 显卡装不下模型而卸载到 CPU（实测掉速数倍）；4096 足够本场景
    ollama_num_ctx: int = 4096
    # 模型常驻时长（避免反复加载，单次加载可达 10–30s）
    ollama_keep_alive: str = "30m"
    # 单次生成的最大输出 token 数：直接决定长文本生成耗时上限
    ollama_num_predict: int = 512
    chroma_persist_dir: str = "data/chroma"
    chroma_collection: str = "xingchen_knowledge"
    # 评测基线 / 关闭重排时的向量召回条数
    rag_top_k: int = 4
    chunk_size: int = 500
    chunk_overlap: int = 50

    # 在线 RAG：向量 Top-K → LLM 重排 → Top-N（已固化，无开关）
    rerank_top_k: int = 6
    rerank_top_n: int = 3

    # Router 专用模型：空 = 复用 llm_model；可指向更小模型（如 qwen3:0.6b）
    router_llm_model: str = ""

    # 回答缓存（精确匹配：改写后问题 + 模型名）
    answer_cache_enabled: bool = True

    def mysql_url(self) -> str:
        """完整 MySQL 连接串（含 mysql_db）。"""
        return self._build_mysql_url(self.mysql_db)

    def mysql_server_url(self) -> str:
        """不含库名的 MySQL 连接串（用于建库）。"""
        return self._build_mysql_url(None)

    def _build_mysql_url(self, database: str | None) -> str:
        return URL.create(
            drivername="mysql+pymysql",
            username=self.mysql_user,
            password=self.mysql_password,
            host=self.mysql_host,
            port=self.mysql_port,
            database=database,
            query={"charset": "utf8mb4"},
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    """返回单例配置对象。"""
    return Settings()
