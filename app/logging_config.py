"""日志配置。

统一日志格式，供全局复用，避免各处重复配置。
"""

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    """初始化根日志配置。"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
