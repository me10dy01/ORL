"""领域异常。

区分错误类型，禁止使用 except Exception: pass 静默吞掉异常。
"""


class AppError(Exception):
    """应用基础异常。"""


class ValidationError(AppError):
    """参数缺失或非法。"""


class NotFoundError(AppError):
    """数据不存在。"""


class DatabaseError(AppError):
    """数据库连接或执行异常。"""
