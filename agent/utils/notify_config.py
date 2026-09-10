# -*- coding: utf-8 -*-
"""Telegram 通知配置的读取、校验与解密（无第三方依赖，agent 与自检脚本共用）。

这个模块存在的三个理由：

1. **配置值可能是密文**。``config/config.json`` 是 MFAAvalonia 的 "Default"
   配置本体（MFA 源码 ``ConfigurationManager``：Default → 文件名 ``config``），
   用户在 MFA「设置 → 外部通知 → Telegram」里填的 token / chat_id 会被
   ``SimpleEncryptionHelper.Encrypt`` 加密后写回这个文件：
   DPAPI(CurrentUser) 保护后再 base64，形如 ``AQAAANCMnd8BFdERjHoAwE/Cl+sB...``。
   直接拿去发消息必然 404，所以这里先解密：DPAPI → AES 设备密钥
   （与 MaaGumballs 不思议迷宫小助手的 ``simpleEncryption`` 同一条链，见 ``mfa_crypto.py``）。
2. **格式**要能一眼看出错在哪，而不是笼统报「为空」。
3. **错误码**要翻译成人话（404/400/429 各自含义完全不同）。
"""

import base64
import re

try:
    from . import mfa_crypto
except ImportError:  # 直接按文件路径加载时（自检脚本）
    import mfa_crypto

ENABLED_KEY = "ExternalNotificationEnabled"
TOKEN_KEY = "ExternalNotificationTelegramBotToken"
CHAT_ID_KEY = "ExternalNotificationTelegramChatId"

# 我们自己的明文通知配置（MFA 不管理这个文件，永远不会被加密/覆盖）
NOTIFY_FILE = "config/notify.json"
# MFA 的 Default 配置文件（= agent 传统上读的文件，值可能是密文）
MFA_CONFIG_FILE = "config/config.json"

# BotFather 发的 token：<bot_id 数字>:<35 位左右的随机串>，总长约 46 字符
BOT_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")
# chat_id：个人是正数，群组/频道是 -100... 开头
CHAT_ID_RE = re.compile(r"^-?\d+$")

# DPAPI blob 头部（CryptProtectData 的标志性前缀），base64 后正是 "AQAAANCMnd8BFdERjHoAwE/Cl+sB..."
DPAPI_MAGIC = bytes.fromhex("01000000d08c9ddf0115d1118c7a00c04fc297eb")

SOURCE_PLAINTEXT = "明文"
SOURCE_MFA_DECRYPTED = "MFAAvalonia 加密值（已在本机解密）"
SOURCE_MFA_LOCKED = "MFAAvalonia 加密值（本机解密失败）"


def mask(value: str, keep: int = 4) -> str:
    """打日志用：只露头尾，绝不整串输出。"""
    value = str(value or "")
    if len(value) <= keep * 2:
        return f"<长度{len(value)}>"
    return f"{value[:keep]}…{value[-keep:]}（长度{len(value)}）"


def is_mfa_ciphertext(value) -> bool:
    """判断是不是 MFAAvalonia 加密后的密文（DPAPI blob 的 base64）。

    只做结构判断：能 base64 解码 + 以 DPAPI 头开头。明文 token 带 ``:``，
    永远不可能被 base64 合法解码成这个前缀，所以不会误判。
    """
    text = str(value or "").strip()
    if len(text) < 60 or not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", text):
        return False
    try:
        raw = base64.b64decode(text, validate=True)
    except Exception:
        return False
    return raw.startswith(DPAPI_MAGIC)


def looks_encrypted(value) -> bool:
    """值是否可能是密文：base64 字符集且足够长（DPAPI blob 或 AES 密文都是 base64）。

    明文 bot token 带 ``:``、chat_id 是纯数字，都过不了 base64 字符集这一关。
    """
    text = str(value or "").strip()
    if is_mfa_ciphertext(text):
        return True
    return len(text) >= 24 and bool(re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", text))


def decrypt_mfa_value(value):
    """解出 MFAAvalonia 加密的明文；解不开返回 None。

    解密链与 MaaGumballs（不思议迷宫小助手）一致，见 ``utils/mfa_crypto.py``：
    Windows DPAPI → AES(设备密钥) → AES(legacy 设备密钥)。MFA 的
    ``SimpleEncryptionHelper`` 加密时用无附加熵的 CurrentUser DPAPI，所以同机同用户必解；
    DPAPI 抛异常时它退回 AES 设备密钥，这里也照做。
    """
    return mfa_crypto.decrypt(value)


def normalize_value(value):
    """返回 ``(可用值, 来源说明)``：是密文就尝试解密，已是合法明文则原样返回。"""
    text = str(value or "").strip()
    if not text:
        return "", SOURCE_PLAINTEXT
    # 已经是合法形态（bot token / chat_id）就直接用，别做无谓的解密尝试
    if BOT_TOKEN_RE.match(text) or CHAT_ID_RE.match(text):
        return text, SOURCE_PLAINTEXT
    if looks_encrypted(text):
        plain = decrypt_mfa_value(text)
        if plain:
            return plain.strip(), SOURCE_MFA_DECRYPTED
        return text, SOURCE_MFA_LOCKED
    return text, SOURCE_PLAINTEXT


def validate(bot_token: str, chat_id: str) -> list:
    """返回问题清单（空列表 = 配置可用）。每条都是可直接照做的结论。"""
    bot_token = (bot_token or "").strip()
    chat_id = (chat_id or "").strip()
    problems = []

    if not bot_token:
        problems.append(
            f"{TOKEN_KEY} 为空：需要在 MFA「设置 → 外部通知 → Telegram」里填写，"
            f"或直接写进 {NOTIFY_FILE}。"
        )
    elif not BOT_TOKEN_RE.match(bot_token):
        problems.append(
            f"{TOKEN_KEY} 格式非法（当前 {mask(bot_token)}）。"
            "正确形态是「数字ID:35位随机串」共约 46 字符，例如 123456789:AAH...；"
            "常见错误是把 Telegram 登录链接 token、或 MFA 加密后的密文（AQAAAN… 那类 "
            "300+ 字符的串）粘了进来。"
        )

    if not chat_id:
        problems.append(
            f"{CHAT_ID_KEY} 为空：填接收通知的数字 ID"
            "（自己的用户 ID 为正数；群组/频道为 -100… 开头）。"
        )
    elif not CHAT_ID_RE.match(chat_id):
        problems.append(
            f"{CHAT_ID_KEY} 必须是纯数字（当前 {mask(chat_id, keep=6)}）。"
            "常见错误是把 bot token 或密文粘到了这一栏。"
        )

    return problems


def mfa_hint(bot_token: str, chat_id: str) -> str:
    """如果是「MFA 密文但本机解不开」，给出根因和出路。"""
    _, token_src = normalize_value(bot_token)
    _, chat_src = normalize_value(chat_id)
    if SOURCE_MFA_LOCKED not in (token_src, chat_src):
        return ""
    return (
        f"读到的是 MFAAvalonia 加密的密文，本机 DPAPI 解不开（换机器/换 Windows 用户"
        f"就会这样）。解决：在 {NOTIFY_FILE} 里填明文，"
        f"例如 {{\"{TOKEN_KEY}\": \"123456789:AAH...\", \"{CHAT_ID_KEY}\": \"7200170648\"}}。"
        "这个文件 MFA 不管，不会被重新加密。"
    )


def describe(problems: list) -> str:
    """把问题清单拼成一段可直接贴进日志/对话的说明。"""
    if not problems:
        return "Telegram 通知配置格式检查通过。"
    lines = ["Telegram 通知配置有 " + str(len(problems)) + " 处问题，通知无法发送："]
    lines.extend(f"  {i}. {p}" for i, p in enumerate(problems, 1))
    lines.append("  修好后可运行包内「检查通知配置.bat」验证。")
    return "\n".join(lines)


def explain_http_error(status_code: int, body: str = "") -> str:
    """把 Telegram 的 HTTP 状态码翻译成人话。"""
    if status_code == 404:
        return "404 Not Found = Telegram 不认识这个 bot token（token 写错或 bot 已删除）。"
    if status_code == 401:
        return "401 Unauthorized = bot token 无效。"
    if status_code == 400:
        return (
            "400 Bad Request = 请求参数被拒，最常见是 chat_id 不存在，"
            "或你还没跟这个 bot 说过话（bot 不能主动私聊陌生用户，先去私聊发一句 /start）。"
        )
    if status_code == 403:
        return "403 Forbidden = bot 被对方拉黑或不允许发消息。"
    if status_code == 429:
        return "429 Too Many Requests = 触发频率限制，稍后重试。"
    return f"HTTP {status_code}：{body[:200]}"
