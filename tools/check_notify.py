#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram 通知配置自检（不需要游戏、不需要 MFA 即可运行）。

用法（在包目录下）：
    python\\python.exe tools\\check_notify.py
Windows 也可以直接双击包内「检查通知配置.bat」。

它做三件事，逐步给出结论：
  1. 读 config/config.json（+ 叠加 config/notify.json），检查 token / chat_id；
     若发现是 MFAAvalonia 加密的密文，会先尝试用本机 DPAPI 解出明文；
  2. 调 getMe 确认 Telegram 认识这个 bot（token 是否有效）；
  3. 真发一条测试消息，把 HTTP 状态码翻译成可执行的原因。

只用标准库，配置格式的判定与 agent 运行时共用 agent/utils/notify_config.py，
避免"脚本说没问题、运行时说有问题"。
"""

import importlib.util
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "config.json")
NOTIFY_OVERRIDE_PATH = os.path.join(PROJECT_ROOT, "config", "notify.json")
NOTIFY_CONFIG_PATH = os.path.join(PROJECT_ROOT, "agent", "utils", "notify_config.py")


def load_notify_config():
    if not os.path.exists(NOTIFY_CONFIG_PATH):
        print(f"[FAIL] 找不到 {NOTIFY_CONFIG_PATH}")
        sys.exit(2)
    spec = importlib.util.spec_from_file_location("notify_config_standalone", NOTIFY_CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"[FAIL] 找不到配置文件：{CONFIG_PATH}")
        print("       请确认在包根目录运行本脚本。")
        sys.exit(2)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def http_get_json(url: str, timeout: int = 15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, _as_dict(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _as_dict(e.read())
    except Exception as e:
        return None, {"error": str(e)}


def http_post_form(url: str, data: dict, timeout: int = 15):
    payload = urllib.parse.urlencode(data).encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=payload, timeout=timeout) as resp:
            return resp.status, _as_dict(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _as_dict(e.read())
    except Exception as e:
        return None, {"error": str(e)}


def _as_dict(raw: bytes) -> dict:
    """Telegram 正常返回 JSON 对象；异常响应可能是 HTML/纯文本，统一包成 dict。"""
    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text)
    except Exception:
        return {"raw": text[:300]}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def load_overrides() -> dict:
    """config/notify.json：MFA 不管理的明文通知配置（非空值优先）。"""
    if not os.path.exists(NOTIFY_OVERRIDE_PATH):
        return {}
    try:
        with open(NOTIFY_OVERRIDE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[WARN] 读取 {NOTIFY_OVERRIDE_PATH} 失败：{e}")
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if not str(k).startswith("_") and str(v or "").strip()}


def main() -> int:
    nc = load_notify_config()
    config = load_config()
    overrides = load_overrides()

    # agent 运行时的实际取值规则：config.json 打底，notify.json 的非空值覆盖
    effective = dict(config)
    effective.update(overrides)

    enabled = effective.get(nc.ENABLED_KEY, "")
    token_raw = str(effective.get(nc.TOKEN_KEY, "") or "").strip()
    chat_id_raw = str(effective.get(nc.CHAT_ID_KEY, "") or "").strip()
    token, token_src = nc.normalize_value(token_raw)
    chat_id, chat_src = nc.normalize_value(chat_id_raw)

    print("=" * 64)
    print("Telegram 通知配置自检")
    print("=" * 64)
    print(f"配置文件        : {CONFIG_PATH}")
    print(f"明文覆盖文件    : {NOTIFY_OVERRIDE_PATH}（{'有，%d 项生效' % len(overrides) if overrides else '无'}）")
    print(f"{nc.ENABLED_KEY} : {enabled!r}")
    print(f"{nc.TOKEN_KEY}   : {nc.mask(token)}  [{token_src}]")
    print(f"{nc.CHAT_ID_KEY} : {nc.mask(chat_id)}  [{chat_src}]")
    print("-" * 64)

    # 密文（MFA 界面里填的值）优先解释清楚，否则用户看到的长度信息会误导排查
    if nc.is_mfa_ciphertext(token_raw) or nc.is_mfa_ciphertext(chat_id_raw):
        print("注意：配置里是 MFAAvalonia 加密后的密文（MFA 把界面里填的值 DPAPI 加密后")
        print("      写回 config/config.json，只有填值那台机器+那个 Windows 用户能解开）。")
        hint = nc.mfa_hint(token_raw, chat_id_raw)
        if hint:
            print("      " + hint)
        else:
            print("      本机已成功解密 → 能用，继续往下测。")
        print("-" * 64)

    print("[1/3] 检查配置格式 ...")
    problems = nc.validate(token, chat_id)
    if problems:
        print(nc.describe(problems))
        return 1
    print("      OK：格式合法")

    if str(enabled).strip().lower() not in ("telegram",):
        print(f"      提示：{nc.ENABLED_KEY} 当前是 {enabled!r}，需要填 \"Telegram\" 才会发通知。")

    print("[2/3] 调用 getMe 验证 token ...")
    status, body = http_get_json(f"https://api.telegram.org/bot{token}/getMe")
    if status != 200 or not body.get("ok"):
        print(f"      FAIL（HTTP {status}）：{body}")
        print("      " + nc.explain_http_error(status or 0, json.dumps(body, ensure_ascii=False)))
        return 1
    bot = body.get("result", {})
    print(f"      OK：bot = @{bot.get('username')}（{bot.get('first_name')}）")

    print("[3/3] 发送测试消息 ...")
    status, body = http_post_form(
        f"https://api.telegram.org/bot{token}/sendMessage",
        {"chat_id": chat_id, "text": "Maa_MHXY_MG 通知自检：如果你看到这条消息，说明配置正确。"},
    )
    if status == 200 and body.get("ok"):
        print(f"      OK：已发送给 {body.get('result', {}).get('chat', {}).get('title') or chat_id}")
        print("-" * 64)
        print("结论：配置完全可用，妖王出现时会推送到这里。")
        return 0

    print(f"      FAIL（HTTP {status}）：{body}")
    print("      " + nc.explain_http_error(status or 0, json.dumps(body, ensure_ascii=False)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
