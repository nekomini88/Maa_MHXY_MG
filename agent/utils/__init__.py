from .logger import  logger
from .utils import LocalStorage
from .SendKingsoftDocs import SendJinSan

try:
    from .message import send_message, read_config
except ImportError:
    logger.warning(
        "message 模块初始化失败（依赖缺失？）。外部通知功能不可用。"
    )

    def send_message(*args, **kwargs) -> bool:
        """兜底实现：message 模块加载失败时，明确记录并返回 False，而非让调用方崩溃。"""
        logger.warning("[message] send_message 不可用（message 模块初始化失败）。通知未发送。")
        return False

    def read_config() -> bool:
        logger.warning("[message] read_config 不可用（message 模块初始化失败）。")
        return False