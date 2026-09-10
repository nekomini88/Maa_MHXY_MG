# -*- coding: utf-8 -*-
"""Telegram 通知配置校验 + 发送前拦截的测试。

回归背景：用户把 Telegram 登录链接的 token（300+ 字符）粘进了
ExternalNotificationTelegramBotToken / ChatId，结果 agent 只回一句
「token 为空或 chat_id 为空」，排查方向被带偏；同时 Telegram 侧返回 404。
这里的用例保证：字段「非空但填错」必须被精确指出，且不合法时不许发请求。
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_UTILS = REPO_ROOT / "agent" / "utils"
NOTIFY_CONFIG_PATH = AGENT_UTILS / "notify_config.py"
MESSAGE_PATH = AGENT_UTILS / "message.py"

VALID_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
VALID_CHAT_ID = "7200170648"
# 用户实际粘进来的东西：Telegram 登录链接 token
LOGIN_TOKEN = "AQAAAN" + "x" * 346


def load_notify_config():
    spec = importlib.util.spec_from_file_location("notify_config_under_test", NOTIFY_CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _CapturingLogger:
    def __init__(self):
        self.messages = []

    def _rec(self, level):
        def handler(message, *args, **kwargs):
            self.messages.append((level, str(message)))
        return handler

    def __getattr__(self, item):
        return self._rec(item)

    def text(self):
        return "\n".join(m for _, m in self.messages)


class NotifyConfigValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nc = load_notify_config()

    def test_valid_config_passes(self):
        self.assertEqual(self.nc.validate(VALID_TOKEN, VALID_CHAT_ID), [])

    def test_group_chat_id_with_minus_passes(self):
        self.assertEqual(self.nc.validate(VALID_TOKEN, "-1001234567890"), [])

    def test_login_token_in_token_field_is_reported(self):
        problems = self.nc.validate(LOGIN_TOKEN, VALID_CHAT_ID)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(self.nc.TOKEN_KEY, problems[0])
        self.assertIn("格式非法", problems[0])

    def test_token_pasted_into_chat_id_is_reported(self):
        problems = self.nc.validate(VALID_TOKEN, VALID_TOKEN)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("纯数字", problems[0])

    def test_both_fields_empty_reports_both(self):
        problems = self.nc.validate("", "")
        self.assertEqual(len(problems), 2)

    def test_whitespace_only_counts_as_empty(self):
        problems = self.nc.validate("   ", "  ")
        self.assertEqual(len(problems), 2)

    def test_mask_never_leaks_full_value(self):
        masked = self.nc.mask(LOGIN_TOKEN)
        self.assertNotIn(LOGIN_TOKEN, masked)
        self.assertIn("长度352", masked)

    def test_describe_lists_every_problem(self):
        text = self.nc.describe(self.nc.validate(LOGIN_TOKEN, LOGIN_TOKEN))
        self.assertIn("2 处问题", text)
        self.assertIn("检查通知配置.bat", text)

    def test_describe_ok_message(self):
        self.assertIn("通过", self.nc.describe([]))

    def test_http_error_explanations(self):
        self.assertIn("bot token", self.nc.explain_http_error(404))
        self.assertIn("chat_id", self.nc.explain_http_error(400))
        self.assertIn("频率", self.nc.explain_http_error(429))


class SendGuardTests(unittest.TestCase):
    """send_telegram 必须在发请求前拦截坏配置，并给出可执行报错。"""

    def setUp(self):
        self.nc = load_notify_config()
        self.logger = _CapturingLogger()
        fake_utils = types.ModuleType("utils")
        fake_utils.logger = self.logger
        sys.modules["utils"] = fake_utils
        sys.path.insert(0, str(AGENT_UTILS))

        spec = importlib.util.spec_from_file_location("message_under_test", MESSAGE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.message = module

    def tearDown(self):
        sys.modules.pop("utils", None)

    def test_invalid_config_blocks_request(self):
        import requests

        calls = []
        original_post = requests.post
        requests.post = lambda *a, **k: calls.append((a, k))
        try:
            self.message.config = {
                self.nc.TOKEN_KEY: LOGIN_TOKEN,
                self.nc.CHAT_ID_KEY: LOGIN_TOKEN,
                self.nc.ENABLED_KEY: "Telegram",
            }
            ok = self.message.send_message("妖王出现", "测试")
        finally:
            requests.post = original_post

        self.assertFalse(ok)
        self.assertEqual(calls, [], "配置非法时不应发起 HTTP 请求")
        text = self.logger.text()
        self.assertIn("格式非法", text)
        self.assertIn("纯数字", text)
        self.assertIn("检查通知配置.bat", text)

    def test_valid_config_sends_request(self):
        import requests

        captured = {}

        def fake_post(url, data=None, timeout=None):
            captured["url"] = url
            captured["data"] = data

            class R:
                status_code = 200
                text = '{"ok":true}'

            return R()

        original_post = requests.post
        requests.post = fake_post
        try:
            self.message.config = {
                self.nc.TOKEN_KEY: VALID_TOKEN,
                self.nc.CHAT_ID_KEY: VALID_CHAT_ID,
                self.nc.ENABLED_KEY: "Telegram",
            }
            ok = self.message.send_message("妖王出现", "测试")
        finally:
            requests.post = original_post

        self.assertTrue(ok)
        self.assertIn(VALID_TOKEN, captured["url"])
        self.assertEqual(captured["data"]["chat_id"], VALID_CHAT_ID)

    def test_404_response_explains_token(self):
        import requests

        class R:
            status_code = 404
            text = '{"ok":false,"error_code":404,"description":"Not Found"}'

        original_post = requests.post
        requests.post = lambda *a, **k: R()
        try:
            self.message.config = {
                self.nc.TOKEN_KEY: VALID_TOKEN,
                self.nc.CHAT_ID_KEY: VALID_CHAT_ID,
                self.nc.ENABLED_KEY: "Telegram",
            }
            ok = self.message.send_message("妖王出现", "测试")
        finally:
            requests.post = original_post

        self.assertFalse(ok)
        self.assertIn("原因解读", self.logger.text())
        self.assertIn("bot token", self.logger.text())


class YaowangLoggingTests(unittest.TestCase):
    """妖王模块只能有一个日志出口：不得 remove() 全局 sink，也不得每条写两遍。"""

    @classmethod
    def setUpClass(cls):
        import ast

        cls.source = (REPO_ROOT / "agent" / "custom" / "recognition" / "yaowang.py").read_text(
            encoding="utf-8"
        )
        cls.tree = ast.parse(cls.source)

    def test_does_not_call_logger_remove(self):
        """AST 检查真实调用（注释里提到 remove() 不算）。"""
        import ast

        offenders = []
        for node in ast.walk(self.tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "remove"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in ("logger", "_ylogger", "_ylog")
            ):
                offenders.append(getattr(node, "lineno", "?"))
        self.assertEqual(
            offenders, [], "yaowang 不得移除全局 loguru sink（会吞掉其它模块日志）"
        )

    def test_does_not_import_loguru(self):
        """不要另开 loguru logger；日志统一走 utils.logger。"""
        import ast

        offenders = [
            node.lineno
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("loguru")
        ]
        offenders += [
            node.lineno
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Import)
            and any(a.name.startswith("loguru") for a in node.names)
        ]
        self.assertEqual(offenders, [], "不要另开一个 loguru logger 实例")

    def test_helpers_call_logger_exactly_once(self):
        import ast

        for node in self.tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_ylog_"):
                if node.name == "_ylog_fmt":
                    continue
                calls = [
                    n
                    for n in ast.walk(node)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "logger"
                ]
                self.assertEqual(
                    len(calls), 1, f"{node.name} 里 logger 调用 {len(calls)} 次，应为 1 次"
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
