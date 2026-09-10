# -*- coding: utf-8 -*-
"""日志体积策略测试：20 MB 轮转 / 7 天保留 / zip 压缩 / 启动清理。

回归背景：妖王日志原来是 ``rotation="1 day" + retention="3 days"`` 且不压缩，
`debug/custom` 下日志和压缩包混放，排查时目录能堆到几十上百 MB。
"""

import importlib.util
import os
import sys
import time
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_UTILS = REPO_ROOT / "agent" / "utils"
LOG_POLICY_PATH = AGENT_UTILS / "log_policy.py"

DAY = 24 * 60 * 60


def load_log_policy():
    if str(AGENT_UTILS) not in sys.path:
        sys.path.insert(0, str(AGENT_UTILS))
    spec = importlib.util.spec_from_file_location("log_policy_under_test", LOG_POLICY_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PolicyDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.lp = load_log_policy()

    def test_rotation_is_20mb(self):
        self.assertEqual(self.lp.ROTATION, "20 MB")

    def test_retention_is_7_days(self):
        self.assertEqual(self.lp.RETENTION, "7 days")
        self.assertEqual(self.lp.RETENTION_DAYS, 7)

    def test_compression_is_zip(self):
        self.assertEqual(self.lp.COMPRESSION, "zip")

    def test_sink_kwargs_exposes_three_settings(self):
        kwargs = self.lp.sink_kwargs()
        self.assertEqual(
            set(kwargs), {"rotation", "retention", "compression"}, "sink 参数必须齐三项"
        )
        self.assertEqual(kwargs["rotation"], self.lp.ROTATION)
        self.assertEqual(kwargs["retention"], self.lp.RETENTION)
        self.assertEqual(kwargs["compression"], self.lp.COMPRESSION)


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.lp = load_log_policy()
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _make(self, name, age_days, content="line\n"):
        path = self.dir / name
        path.write_text(content, encoding="utf-8")
        when = time.time() - age_days * DAY
        os.utime(path, (when, when))
        return path

    def test_prune_compresses_old_and_deletes_expired(self):
        active = self._make("yaowang.log", 30)  # 常驻文件：绝不碰
        today = self._make(time.strftime("%Y-%m-%d") + ".log", 30)  # 今天在写：不碰
        expired = self._make("2026-08-01.log", 8)  # 超过 7 天：删除
        recent = self._make("2026-09-05.log", 3, "要保留的内容\n")  # 未过期但旧：压缩
        expired_zip = self._make("2026-08-01.log.zip", 9)
        other = self._make("notes.txt", 30)  # 非日志：不碰

        compressed, deleted, errors = self.lp.prune_old_logs(self.dir, now=time.time())

        self.assertEqual(errors, [])
        self.assertEqual(compressed, 1)
        self.assertEqual(deleted, 2)
        self.assertTrue(active.exists(), "正在写入的 yaowang.log 不能被清理")
        self.assertTrue(today.exists(), "当天日志正在被写，不能清理")
        self.assertTrue(other.exists(), "非日志文件不能被清理")
        self.assertFalse(expired.exists())
        self.assertFalse(expired_zip.exists())
        self.assertFalse(recent.exists(), "压缩后原文件应删除")

        archive = self.dir / "2026-09-05.log.zip"
        self.assertTrue(archive.exists())
        with zipfile.ZipFile(archive) as zf:
            self.assertIn("要保留的内容", zf.read("2026-09-05.log").decode("utf-8"))

    def test_second_run_is_noop(self):
        self._make("2026-09-05.log", 3)
        self.lp.prune_old_logs(self.dir, now=time.time())
        compressed, deleted, errors = self.lp.prune_old_logs(self.dir, now=time.time())
        self.assertEqual((compressed, deleted, errors), (0, 0, []))

    def test_missing_dir_is_safe(self):
        self.assertEqual(
            self.lp.prune_old_logs(str(self.dir / "not-here")), (0, 0, [])
        )

    def test_zip_failure_is_reported_not_raised(self):
        target = self._make("2026-09-05.log", 3)
        original = zipfile.ZipFile

        class Boom:
            def __init__(self, *a, **k):
                raise OSError("文件被占用")

        zipfile.ZipFile = Boom
        try:
            compressed, deleted, errors = self.lp.prune_old_logs(self.dir, now=time.time())
        finally:
            zipfile.ZipFile = original

        self.assertEqual(compressed, 0)
        self.assertEqual(deleted, 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("2026-09-05.log", errors[0])
        self.assertTrue(target.exists(), "压缩失败时不能删掉原文件")

    def test_compress_disabled_keeps_file(self):
        recent = self._make("2026-09-05.log", 3)
        compressed, deleted, errors = self.lp.prune_old_logs(
            self.dir, compress=False, now=time.time()
        )
        self.assertEqual((compressed, deleted, errors), (0, 0, []))
        self.assertTrue(recent.exists())


class WiringTests(unittest.TestCase):
    """策略必须真的接上：妖王 sink 用它、启动时执行清理。"""

    def setUp(self):
        self.yaowang = (REPO_ROOT / "agent" / "custom" / "recognition" / "yaowang.py").read_text(
            encoding="utf-8"
        )
        self.logger = (AGENT_UTILS / "logger.py").read_text(encoding="utf-8")

    def test_yaowang_sink_uses_shared_policy(self):
        self.assertIn("from utils import log_policy", self.yaowang)
        self.assertIn("**_YAOWANG_LOG_SINK", self.yaowang)
        self.assertNotIn('retention="3 days"', self.yaowang, "不应再硬编码旧保留期")
        self.assertNotIn('rotation="1 day"', self.yaowang, "轮转口径应来自 log_policy")

    def test_startup_runs_prune(self):
        self.assertIn("log_policy.prune_old_logs", self.logger)

    def test_prune_failure_cannot_break_startup(self):
        """清理异常不能让 logger 初始化失败（日志模块坏了 agent 就起不来）。"""
        self.assertIn("except Exception:", self.logger)


if __name__ == "__main__":
    unittest.main(verbosity=2)
