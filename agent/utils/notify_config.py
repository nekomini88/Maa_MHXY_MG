# -*- coding: utf-8 -*-
"""Telegram 通知配置的校验规则（无第三方依赖，agent 与自检脚本共用）。

为什么单独抽出来：历史上配置填错时（把 Telegram 登录链接的 token 粘进
bot token / chat_id 字段），代码只回一句「token 为空或 chat_id 为空」，
排查方向完全跑偏。这里把「格式是否合法」的判定集中到一处，
agent 运行时和工具脚本给出的是同一套、可执行的结论。
"""

import re

ENABLED_KEY = "ExternalNotificationEnabled"
TOKEN_KEY = "ExternalNotificationTelegramBotToken"
CHAT_ID_KEY = "ExternalNotificationTelegramChatId"

# BotFather 发的 token：<bot_id 数字>:<35 位左右的随机串>，总长约 46 字符
BOT_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")
# chat_id：个人是正数，群组/频道是 -100... 开头
CHAT_ID_RE = re.compile(r"^-?\d+$")


def mask(value: str, keep: int = 4) -> str:
    """打日志用：只露头尾，绝不整串输出。"""
    value = str(value or "")
    if len(value) <= keep * 2:
        return f"<长度{len(value)}>"
    return f"{value[:keep]}…{value[-keep:]}（长度{len(value)}）"


def validate(bot_token: str, chat_id: str) -> list:
    """返回问题清单（空列表 = 配置可用）。每条都是可直接照做的结论。"""
    bot_token = (bot_token or "").strip()
    chat_id = (chat_id or "").strip()
    problems = []

    if not bot_token:
        problems.append(
            f"{TOKEN_KEY} 为空：需要在 config/config.json 里填 BotFather 给的 bot token。"
        )
    elif not BOT_TOKEN_RE.match(bot_token):
        problems.append(
            f"{TOKEN_KEY} 格式非法（当前 {mask(bot_token)}）。"
            "正确形态是「数字ID:35位随机串」共约 46 字符，例如 123456789:AAH...；"
            "常见错误是把 Telegram 登录链接 token（AQAAAN… 那类 300+ 字符的串）"
            "或别的内容粘了进来。"
        )

    if not chat_id:
        problems.append(
            f"{CHAT_ID_KEY} 为空：填接收通知的数字 ID"
            "（自己的用户 ID 为正数；群组/频道为 -100… 开头）。"
        )
    elif not CHAT_ID_RE.match(chat_id):
        problems.append(
            f"{CHAT_ID_KEY} 必须是纯数字（当前 {mask(chat_id, keep=6)}）。"
            "常见错误是把 bot token 或登录 token 粘到了这一栏。"
        )

    return problems


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
