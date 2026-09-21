"""集中配置管理。

所有运行参数统一在此处通过环境变量 / .env 注入，
禁止在业务代码中硬编码端口、地址、密钥等敏感信息。
"""

from functools import lru_cache
from typing import Literal

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
    app_env: str = "dev"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    # MySQL
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_db: str = "orl_dev"

    # Ollama / RAG（Phase 04 baseline）
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
    rag_top_k: int = 4
    chunk_size: int = 500
    chunk_overlap: int = 50

    # Rerank（Phase 10）
    rerank_top_k: int = 6
    rerank_top_n: int = 3

    # 统一使用主 LLM 做候选重排：本项目中文知识库实测优于 ONNX cross-encoder，
    # 同时避免维护额外的 reranker 模型与运行时依赖。
    reranker_impl: str = "llm"
    # 在线 RAG 始终启用 LLM 重排（top_k→top_n）。
    rag_rerank_enabled: bool = True

    # 路由策略（Supervisor 模型职责重构）：
    # auto = 规则优先，判定不了/冲突时升级 LLM Router（默认）；
    # rules_only = 只用规则（0 次 LLM）；llm = 旧行为（始终完整提示词调 LLM）
    router_mode: Literal["auto", "rules_only", "llm"] = "auto"
    # Router 专用模型：空 = 复用 llm_model（qwen3:8b）；可指向更小模型（如 qwen3:0.6b）
    router_llm_model: str = ""

    # mixed 路由：RAG 与 Tool 是否并行执行
    # 实测（RTX 4060 Laptop 8GB 单卡）：并行 180.7/205.8s vs 串行 148.7/199.3s，
    # 并行无收益甚至略慢，故默认串行；多卡/远程 LLM/IO 密集场景可设为 true
    mixed_parallel: bool = False

    # QA 逐句归因（citation 与原文对齐）：默认关闭，逐句校验会明显增加延迟
    qa_sentence_alignment: bool = False

    # QA 策略：rule_first（规则先行，能判定则跳过 LLM，默认）| llm（总是 LLM）| rule_only（只用规则）
    qa_mode: Literal["rule_first", "llm", "rule_only"] = "rule_first"

    # 回答缓存（精确匹配：改写后问题 + 模型名）
    answer_cache_enabled: bool = True

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}

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
