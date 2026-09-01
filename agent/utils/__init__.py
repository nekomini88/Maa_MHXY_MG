from .logger import  logger
from .utils import LocalStorage
from .SendKingsoftDocs import SendJinSan

try:
    from .message import send_message, read_config
except ImportError:
    logger.warning(
        "message 模块初始化失败（依赖缺失？）。外部通知功能不可用。"
    )