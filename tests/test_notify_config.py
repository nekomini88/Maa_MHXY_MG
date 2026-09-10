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
    # notify_config 会相对导入 mfa_crypto（解密链）；按文件路径加载时靠这条 sys.path 兜底
    if str(AGENT_UTILS) not in sys.path:
        sys.path.insert(0, str(AGENT_UTILS))
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


class MfaCiphertextTests(unittest.TestCase):
    """回归：config/config.json 是 MFAAvalonia 的 Default 配置本体，
    用户在 MFA 界面填的 token / chat_id 会被 DPAPI 加密后写回该文件。
    agent 必须能识别这种密文并尝试本机解密，而不是拿密文去发消息（必然 404）。
    """

    @classmethod
    def setUpClass(cls):
        import base64

        cls.nc = load_notify_config()
        # 构造一个结构合法的 DPAPI blob（仅头部相同，内容是随机的）
        blob = cls.nc.DPAPI_MAGIC + b"\x11" * 200
        cls.cipher = base64.b64encode(blob).decode()

    def test_detects_dpapi_ciphertext(self):
        self.assertTrue(self.nc.is_mfa_ciphertext(self.cipher))
        self.assertTrue(self.cipher.startswith("AQAAAN"), self.cipher[:8])

    def test_plaintext_values_are_not_ciphertext(self):
        self.assertFalse(self.nc.is_mfa_ciphertext(VALID_TOKEN))
        self.assertFalse(self.nc.is_mfa_ciphertext(VALID_CHAT_ID))
        self.assertFalse(self.nc.is_mfa_ciphertext(""))
        self.assertFalse(self.nc.is_mfa_ciphertext("not-base64-!!!"))

    def test_base64_without_dpapi_header_is_not_ciphertext(self):
        import base64

        self.assertFalse(self.nc.is_mfa_ciphertext(base64.b64encode(b"z" * 300).decode()))

    def test_decrypt_returns_none_off_windows_without_raising(self):
        """Linux/macOS 上必须安全返回 None（只有 Windows 才有 DPAPI）。"""
        if sys.platform == "win32":
            self.skipTest("在 Windows 上无法验证“非 Windows 返回 None”")
        self.assertIsNone(self.nc.decrypt_mfa_value(self.cipher))

    def test_normalize_keeps_plaintext(self):
        value, src = self.nc.normalize_value(VALID_TOKEN)
        self.assertEqual(value, VALID_TOKEN)
        self.assertEqual(src, self.nc.SOURCE_PLAINTEXT)

    def test_normalize_marks_locked_ciphertext(self):
        value, src = self.nc.normalize_value(self.cipher)
        if sys.platform == "win32":
            self.skipTest("Windows 上会用真实 DPAPI 解密，另见集成验证")
        self.assertEqual(value, self.cipher)
        self.assertEqual(src, self.nc.SOURCE_MFA_LOCKED)

    def test_hint_only_for_locked_ciphertext(self):
        self.assertEqual(self.nc.mfa_hint(VALID_TOKEN, VALID_CHAT_ID), "")
        hint = self.nc.mfa_hint(self.cipher, self.cipher)
        if sys.platform != "win32":
            self.assertIn(self.nc.NOTIFY_FILE, hint)


class MfaCryptoAesTests(unittest.TestCase):
    """AES 设备密钥分支：MFA 的 DPAPI 主子失败时的退路。

    参照 MaaGumballs（不思议迷宫小助手）agent/utils/simpleEncryption.py 的实现，
    密钥 = sha256("{稳定系统描述}_{架构}_{设备UUID}_{机器名}")[:32]，AES-256-ECB + PKCS7。
    """

    @classmethod
    def setUpClass(cls):
        cls.nc = load_notify_config()
        cls.mc = sys.modules[cls.nc.mfa_crypto.__name__]

    def _need_crypto(self):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("本机没有 cryptography，AES 分支不参与（Windows 主线走 DPAPI）")

    def test_device_fingerprint_is_stable_and_hex64(self):
        fp = self.mc.generate()
        self.assertEqual(len(fp), 64)
        self.assertEqual(fp, self.mc.generate(), "同一台机器上设备指纹必须稳定")
        self.assertEqual(fp, fp.upper())

    def test_aes_roundtrip_with_device_key(self):
        self._need_crypto()
        cipher = self.mc.aes_encrypt(VALID_TOKEN, self.mc.generate()[:32])
        self.assertIsNotNone(cipher)
        self.assertEqual(self.mc.decrypt(cipher), VALID_TOKEN)

    def test_aes_roundtrip_with_legacy_key(self):
        self._need_crypto()
        cipher = self.mc.aes_encrypt(VALID_CHAT_ID, self.mc.generate_legacy()[:32])
        self.assertIsNotNone(cipher)
        self.assertEqual(self.mc.decrypt(cipher), VALID_CHAT_ID)

    def test_config_layer_decrypts_aes_ciphertext(self):
        """整条链：config 里的 AES 密文 → normalize_value 得到明文 bot token。"""
        self._need_crypto()
        cipher = self.mc.aes_encrypt(VALID_TOKEN, self.mc.generate()[:32])
        value, src = self.nc.normalize_value(cipher)
        self.assertEqual(value, VALID_TOKEN)
        self.assertEqual(src, self.nc.SOURCE_MFA_DECRYPTED)
        self.assertEqual(self.nc.mfa_hint(cipher, ""), "")

    def test_decrypt_of_garbage_returns_none(self):
        self.assertIsNone(self.mc.decrypt(""))
        self.assertIsNone(self.mc.decrypt("not-base64-!!!"))
        self.assertIsNone(self.mc.decrypt("aGVsbG8="))

    def test_dpapi_returns_none_off_windows(self):
        import base64

        if sys.platform == "win32":
            self.skipTest("Windows 上这里会真的尝试 DPAPI")
        blob = base64.b64encode(self.nc.DPAPI_MAGIC + b"\x44" * 200).decode()
        self.assertIsNone(self.mc.dpapi_decrypt(blob))


class SendGuardMfaTests(SendGuardTests):
    """密文解不开时必须拦在发请求之前，并说明根因与出路。"""

    def test_locked_ciphertext_blocks_request_with_hint(self):
        import base64

        import requests

        cipher = base64.b64encode(self.nc.DPAPI_MAGIC + b"\x22" * 200).decode()
        if sys.platform == "win32":
            self.skipTest("Windows 上无法构造“解不开的密文”")

        calls = []
        original_post = requests.post
        requests.post = lambda *a, **k: calls.append((a, k))
        try:
            self.message.config = {
                self.nc.TOKEN_KEY: cipher,
                self.nc.CHAT_ID_KEY: cipher,
                self.nc.ENABLED_KEY: "Telegram",
            }
            ok = self.message.send_telegram("测试")
        finally:
            requests.post = original_post

        self.assertFalse(ok)
        self.assertEqual(calls, [], "密文解不开时不应向 Telegram 发请求")
        text = self.logger.text()
        self.assertIn("MFAAvalonia", text)
        self.assertIn("notify.json", text)


class NotifyOverrideTests(unittest.TestCase):
    """config/notify.json（MFA 不管理）的非空值优先于 config/config.json。"""

    def setUp(self):
        self.nc = load_notify_config()
        self.logger = _CapturingLogger()
        fake_utils = types.ModuleType("utils")
        fake_utils.logger = self.logger
        sys.modules["utils"] = fake_utils
        sys.path.insert(0, str(AGENT_UTILS))
        spec = importlib.util.spec_from_file_location("message_override_under_test", MESSAGE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.message = module

    def tearDown(self):
        sys.modules.pop("utils", None)

    def _project(self, raw_config: dict, notify: dict = None):
        import json
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "config").mkdir(parents=True)
        with open(root / "config" / "config.json", "w", encoding="utf-8") as f:
            json.dump(raw_config, f, ensure_ascii=False)
        if notify is not None:
            with open(root / "config" / "notify.json", "w", encoding="utf-8") as f:
                json.dump(notify, f, ensure_ascii=False)
        self.message._project_root = lambda: root
        return root

    def test_notify_json_overrides_non_empty_values(self):
        self._project(
            {self.nc.TOKEN_KEY: "old", self.nc.CHAT_ID_KEY: "old", self.nc.ENABLED_KEY: "Telegram"},
            {self.nc.TOKEN_KEY: VALID_TOKEN, self.nc.CHAT_ID_KEY: VALID_CHAT_ID},
        )
        self.assertTrue(self.message.read_config())
        self.assertEqual(self.message.config[self.nc.TOKEN_KEY], VALID_TOKEN)
        self.assertEqual(self.message.config[self.nc.CHAT_ID_KEY], VALID_CHAT_ID)

    def test_empty_override_does_not_win(self):
        self._project(
            {self.nc.TOKEN_KEY: "keep-me", self.nc.CHAT_ID_KEY: "keep-me-too"},
            {"ExternalNotificationTelegramBotToken": "", "_说明": ["x"]},
        )
        self.assertTrue(self.message.read_config())
        self.assertEqual(self.message.config[self.nc.TOKEN_KEY], "keep-me")
        self.assertEqual(self.message.config[self.nc.CHAT_ID_KEY], "keep-me-too")

    def test_missing_notify_json_is_fine(self):
        self._project({self.nc.TOKEN_KEY: VALID_TOKEN, self.nc.CHAT_ID_KEY: VALID_CHAT_ID})
        self.assertTrue(self.message.read_config())
        self.assertEqual(self.message.config[self.nc.TOKEN_KEY], VALID_TOKEN)


class CheckNotifyScriptTests(unittest.TestCase):
    """自检脚本对密文要给出正确解释（离线可判定，不用联网）。"""

    def _run(self, config_obj, notify_obj=None):
        import json
        import shutil
        import subprocess
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "config").mkdir(parents=True)
        (root / "tools").mkdir()
        (root / "agent" / "utils").mkdir(parents=True)
        shutil.copy2(REPO_ROOT / "tools" / "check_notify.py", root / "tools" / "check_notify.py")
        shutil.copy2(NOTIFY_CONFIG_PATH, root / "agent" / "utils" / "notify_config.py")
        shutil.copy2(AGENT_UTILS / "mfa_crypto.py", root / "agent" / "utils" / "mfa_crypto.py")
        with open(root / "config" / "config.json", "w", encoding="utf-8") as f:
            json.dump(config_obj, f, ensure_ascii=False)
        if notify_obj is not None:
            with open(root / "config" / "notify.json", "w", encoding="utf-8") as f:
                json.dump(notify_obj, f, ensure_ascii=False)
        proc = subprocess.run(
            [sys.executable, str(root / "tools" / "check_notify.py")],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc

    def test_reports_mfa_ciphertext_without_network(self):
        import base64

        nc = load_notify_config()
        cipher = base64.b64encode(nc.DPAPI_MAGIC + b"\x33" * 200).decode()
        proc = self._run({nc.TOKEN_KEY: cipher, nc.CHAT_ID_KEY: cipher, nc.ENABLED_KEY: "Telegram"})
        out = proc.stdout + proc.stderr
        self.assertIn("MFAAvalonia", out)
        self.assertIn("notify.json", out)
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("AQAAAN" + "Q" * 100, out, "不应打印完整密文")

    def test_plaintext_bad_token_still_reports_format_problem(self):
        nc = load_notify_config()
        proc = self._run({nc.TOKEN_KEY: "YOUR_BOT_TOKEN", nc.CHAT_ID_KEY: "YOUR_CHAT_ID"})
        out = proc.stdout + proc.stderr
        self.assertIn("格式非法", out)
        self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
