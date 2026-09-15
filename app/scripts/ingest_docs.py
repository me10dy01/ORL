"""构建知识库索引。

用法：
    uv run python -m app.scripts.ingest_docs
"""

from app.rag import build_index

if __name__ == "__main__":
    result = build_index()
    print(result)
