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

try:
    from . import notify_config
except ImportError:  # 直接按文件路径加载时（自检脚本）
    import notify_config

config: dict = {}


def _project_root() -> Path:
    """agent/utils/message.py -> 项目根 = 上三级。"""
    return Path(__file__).resolve().parent.parent.parent


def _load_notify_overrides(project_root: Path) -> dict:
    """读取我们自己的明文通知配置 ``config/notify.json``（MFA 不管理该文件）。

    非空值优先于 config/config.json：MFA 会把用户在它界面里填的 token / chat_id
    加密后写回 config/config.json，用户手写的明文在那里随时可能被覆盖，
    所以另留一个 MFA 不碰的文件作为可靠的明文来源。
    """
    path = project_root / "config" / "notify.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not str(k).startswith("_") and str(v or "").strip()}
    except Exception:
        logger.exception(f"读取 {path} 失败，已忽略该文件。")
    return {}


def read_config() -> bool:
    """读取并解析 config/config.json 里的外部通知配置（再叠加 config/notify.json）。

    config.json 位于项目根目录的 ``config/`` 下（与 agent/ 同级）。优先按模块文件
    位置反推项目根，避免依赖进程 cwd，使任何调用方式都能稳定读到配置。
    """
    global config
    project_root = _project_root()
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
        overrides = _load_notify_overrides(project_root)
        if overrides:
            config.update(overrides)
            logger.info(
                f"[message] 已应用 {notify_config.NOTIFY_FILE} 的明文通知配置（{len(overrides)} 项）。"
            )
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
    if not config:
        # 显式提示：即使已尝试读取，config 仍是空
        logger.warning(
            "[message] config 为空（可能是 config/config.json 缺失或未填 ExternalNotificationEnabled）。外部通知未启用。"
        )
        return []
    en = config.get("ExternalNotificationEnabled", False)
    if not en:
        logger.warning(
            "[message] config 中 ExternalNotificationEnabled 为空/未配置，外部通知未启用。"
        )
        return []
    types = [t.strip() for t in str(en).split(",") if t.strip()]
    logger.info(f"[message] 启用的通知通道: {types}")
    return types


def send_telegram(text: str) -> bool:
    """通过 Telegram Bot 发送消息。

    Args:
        text: 要发送的消息正文。
    Returns:
        发送成功返回 True，否则 False。
    """
    raw_token = str(config.get(notify_config.TOKEN_KEY, "") or "").strip()
    raw_chat_id = str(config.get(notify_config.CHAT_ID_KEY, "") or "").strip()

    # MFAAvalonia 会把用户在它界面里填的这两项 DPAPI 加密后写进 config/config.json，
    # 密文直接发必然 404，所以这里先判断/解密，拿到真正的明文再校验。
    bot_token, token_src = notify_config.normalize_value(raw_token)
    chat_id, chat_src = notify_config.normalize_value(raw_chat_id)
    if notify_config.SOURCE_MFA_DECRYPTED in (token_src, chat_src):
        logger.info(
            "[message] 通知配置是 MFAAvalonia 加密值，已在本机解密："
            f"token={notify_config.mask(bot_token)} | chat_id={chat_id}"
        )

    # 校验格式：字段「非空但填错」（或密文解不开）是最常见的情况，笼统报“未配置”会把人带偏。
    problems = notify_config.validate(bot_token, chat_id)
    if problems:
        logger.error(notify_config.describe(problems))
        hint = notify_config.mfa_hint(raw_token, raw_chat_id)
        if hint:
            logger.error(f"[message] {hint}")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
        if resp.status_code == 200:
            logger.info("Telegram 消息推送成功。")
            return True
        logger.error(
            f"[message] Telegram 消息推送失败，状态码：{resp.status_code}，响应：{resp.text[:200]}"
        )
        logger.error(
            "[message] 原因解读：" + notify_config.explain_http_error(resp.status_code, resp.text)
        )
        return False
    except Exception as e:
        logger.error(f"[message] 发送 Telegram 消息失败：{e}")
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