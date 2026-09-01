# -*- coding: utf-8 -*-
"""外部通知模块（参考 MaaGumballs 移植精简版）。

已裁掉 SMTP/钉钉/Qmsg/PushPlus 四种通道，仅保留 Telegram（当前项目需求），
并通过 config/config.json 的 ``ExternalNotificationEnabled`` 决定是否启用。

调用方式：
    from utils import send_message
    send_message("妖王出现", "在【抓鬼】过程中识别到妖王刷新！")
"""

from pathlib import Path
import json

import requests

from utils import logger

config: dict = {}


def read_config() -> bool:
    """读取并解析 config/config.json 里的外部通知配置。

    config.json 位于项目根目录的 ``config/`` 下（与 agent/ 同级）。优先按模块文件
    位置反推项目根，避免依赖进程 cwd，使任何调用方式都能稳定读到配置。
    """
    global config
    # agent/utils/message.py -> 项目根 = 上两级
    here = Path(__file__).resolve()          # <root>/agent/utils/message.py
    project_root = here.parent.parent.parent  # <root>
    config_path = project_root / "config" / "config.json"
    if not config_path.exists():
        # 兜底：按 cwd 再试一次（老用法）
        fallback = Path("./config/config.json")
        config_path = fallback if fallback.exists() else config_path
        if not config_path.exists():
            logger.info(f"未找到 config/config.json（{config_path}），外部通知不可用。")
            return False
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        logger.debug(f"外部通知配置读取成功：{config_path}")
        return True
    except Exception:
        logger.exception("读取 config/config.json 失败，请检查配置文件！")
        return False


def __get_or_read_config() -> None:
    """确保 config 已加载；未加载则先读一次。"""
    global config
    if not config:
        read_config()


def __enabled_types() -> list:
    """返回启用通知通道列表，如 ["Telegram"]。"""
    __get_or_read_config()
    en = config.get("ExternalNotificationEnabled", False)
    if not en:
        return []
    return [t.strip() for t in str(en).split(",") if t.strip()]


def send_telegram(text: str) -> bool:
    """通过 Telegram Bot 发送消息。

    Args:
        text: 要发送的消息正文。
    Returns:
        发送成功返回 True，否则 False。
    """
    bot_token = config.get("ExternalNotificationTelegramBotToken", "").strip()
    chat_id = config.get("ExternalNotificationTelegramChatId", "").strip()

    if not bot_token or not chat_id:
        logger.warning("Telegram bot_token 或 chat_id 未配置，无法发送。")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
        if resp.status_code == 200:
            logger.info("Telegram 消息推送成功。")
            return True
        logger.error(f"Telegram 消息推送失败，状态码：{resp.status_code}")
        return False
    except Exception as e:
        logger.error(f"发送 Telegram 消息失败：{e}")
        return False


def send_message(title: str, text: str = "") -> bool:
    """发送消息主入口。

    Args:
        title: 消息标题（Telegram 下作为消息前缀）。
        text: 消息正文。
    Returns:
        任一通道发送成功返回 True，否则 False。
    """
    __get_or_read_config()
    types = __enabled_types()
    if not types:
        logger.info("未配置外部通知通道（ExternalNotificationEnabled），跳过发送。")
        return False

    # Telegram 没有 title 概念，直接拼接
    full_text = f"{title}: {text}" if text else title

    ok = False
    for t in types:
        if t == "Telegram":
            ok = send_telegram(full_text) or ok
        else:
            logger.info(f"暂不支持的通道类型：{t}")
    return ok