# -*- coding: utf-8 -*-
"""agent/main.py 依赖安装逻辑的单元测试。

覆盖三类回归：
1. pip 配置必须锚定项目根目录（原来跟随 CWD，会写到另一个文件）；
2. 依赖已随包预装时不得调用 pip / 不得联网探测镜像源；
3. 需要安装时优先用包内离线 wheel（--no-index），镜像源只作兜底。
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_main_module():
    """把 agent/main.py 作为独立模块加载（导入时它会 chdir，测试后还原）。"""
    saved_cwd = os.getcwd()
    module_path = REPO_ROOT / "agent" / "main.py"
    spec = importlib.util.spec_from_file_location("agent_main_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        os.chdir(saved_cwd)
    return module


class RecordingLogger:
    def __init__(self):
        self.messages = []

    def _record(self, level):
        def handler(message, *args, **kwargs):
            self.messages.append((level, str(message)))
        return handler

    def __getattr__(self, item):
        return self._record(item)

    def text(self):
        return "\n".join(m for _, m in self.messages)


class DependencySetupTests(unittest.TestCase):
    def setUp(self):
        self.module = load_main_module()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)

        original_root = self.module.project_root_dir
        self.module.project_root_dir = str(self.root)
        self.addCleanup(lambda: setattr(self.module, "project_root_dir", original_root))

        original_logger = self.module.logger
        self.logger = RecordingLogger()
        self.module.logger = self.logger
        self.addCleanup(lambda: setattr(self.module, "logger", original_logger))

        (self.root / "interface.json").write_text('{"version": "v0.1.1"}', encoding="utf-8")
        (self.root / "requirements.txt").write_text("colorama\n", encoding="utf-8")

    # ---- pip 配置路径 -------------------------------------------------
    def test_read_pip_config_writes_under_project_root(self):
        config = self.module.read_pip_config()
        config_file = self.root / "config" / "pip_config.json"
        self.assertTrue(config_file.exists(), "配置应写进项目根目录下的 config/")
        self.assertIn("enable_pip_install", config)
        self.assertIn("mirror", config)

    def test_read_pip_config_ignores_cwd(self):
        """从别的 CWD 调用也必须命中同一个文件（回归：曾经写到 CWD/config）。"""
        other = self.root / "elsewhere"
        other.mkdir()
        previous = os.getcwd()
        os.chdir(other)
        try:
            self.module.read_pip_config()
        finally:
            os.chdir(previous)
        self.assertTrue((self.root / "config" / "pip_config.json").exists())
        self.assertFalse((other / "config" / "pip_config.json").exists())

    # ---- 依赖判定 -----------------------------------------------------
    def test_missing_requirements_reports_absent_packages(self):
        missing = self.module.missing_requirements(installed=["colorama", "numpy"])
        self.assertIn("maafw", missing)
        self.assertNotIn("colorama", missing)

    def test_missing_requirements_empty_when_all_installed(self):
        missing = self.module.missing_requirements(
            installed=list(self.module.REQUIRED_DISTRIBUTIONS)
        )
        self.assertEqual(missing, [])

    def test_missing_requirements_normalizes_underscores_and_case(self):
        missing = self.module.missing_requirements(
            installed=list(self.module.REQUIRED_DISTRIBUTIONS) + ["pydantic_core".upper()]
        )
        self.assertEqual(missing, [])

    def test_find_local_wheel_dir(self):
        self.assertIsNone(self.module.find_local_wheel_dir())
        wheels = self.root / "deps"
        wheels.mkdir()
        (wheels / "colorama-0.4.6-py2.py3-none-any.whl").write_text("x")
        self.assertEqual(Path(self.module.find_local_wheel_dir()), wheels)

    # ---- 跳过 / 安装 分支 ---------------------------------------------
    def test_skips_pip_when_dependencies_already_installed(self):
        self.module.missing_requirements = lambda *a, **k: []
        calls = []
        self.module.install_requirements = lambda *a, **k: calls.append("install") or True
        self.module.check_mirror_reachable = lambda *a, **k: calls.append("mirror") or True

        self.module.check_and_install_dependencies()

        self.assertEqual(calls, [], "依赖齐全时不应调用 pip，也不应探测镜像源")
        self.assertIn("跳过依赖安装", self.logger.text())
        config = json.loads((self.root / "config" / "pip_config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["last_version"], "v0.1.1")

    def test_disabled_pip_install_does_not_install(self):
        (self.root / "config").mkdir()
        (self.root / "config" / "pip_config.json").write_text(
            json.dumps({"enable_pip_install": False, "last_version": "v0.1.0"}),
            encoding="utf-8",
        )
        self.module.missing_requirements = lambda *a, **k: ["maafw"]
        calls = []
        self.module.install_requirements = lambda *a, **k: calls.append("install") or True

        self.module.check_and_install_dependencies()

        self.assertEqual(calls, [], "enable_pip_install=false 时不得安装")
        self.assertIn("缺少依赖: maafw", self.logger.text())
        self.assertIn("Pip 依赖安装已禁用", self.logger.text())

    def test_install_requirements_uses_offline_wheels_first(self):
        wheels = self.root / "deps"
        wheels.mkdir()
        (wheels / "colorama-0.4.6-py2.py3-none-any.whl").write_text("x")

        captured = {}

        def fake_run_pip(cmd, operation_name):
            captured["cmd"] = list(cmd)
            return True

        self.module._run_pip_command = fake_run_pip
        self.module.check_mirror_reachable = lambda *a, **k: self.fail("离线可用时不应探测镜像源")

        self.assertTrue(self.module.install_requirements(pip_config={"mirror": "http://m"}))
        self.assertIn("--no-index", captured["cmd"])
        self.assertIn("--find-links", captured["cmd"])
        self.assertIn(str(wheels), captured["cmd"])

    def test_install_requirements_falls_back_to_mirror(self):
        captured = {}

        def fake_run_pip(cmd, operation_name):
            captured["cmd"] = list(cmd)
            return True

        self.module._run_pip_command = fake_run_pip
        self.module.get_available_mirror = lambda cfg: "https://mirror.example/simple"

        self.assertTrue(self.module.install_requirements(pip_config={}))
        self.assertIn("-i", captured["cmd"])
        self.assertIn("https://mirror.example/simple", captured["cmd"])

    # ---- 镜像源探测 ---------------------------------------------------
    def test_get_available_mirror_returns_first_reachable(self):
        self.module.check_mirror_reachable = lambda mirror, timeout=8: mirror.endswith("mirror2")
        chosen = self.module.get_available_mirror(
            {"mirror": "http://mirror1", "backup_mirrors": ["http://mirror2"]}
        )
        self.assertEqual(chosen, "http://mirror2")

    def test_get_available_mirror_none_when_all_unreachable(self):
        self.module.check_mirror_reachable = lambda mirror, timeout=8: False
        self.assertIsNone(self.module.get_available_mirror({"mirror": "http://mirror1"}))

    def test_check_mirror_reachable_true_on_http_ok(self):
        import urllib.request

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        original = urllib.request.urlopen
        urllib.request.urlopen = lambda request, timeout=0: FakeResponse()
        try:
            self.assertTrue(self.module.check_mirror_reachable("https://mirror.example/simple"))
        finally:
            urllib.request.urlopen = original

    def test_check_mirror_reachable_false_on_error(self):
        import urllib.request

        original = urllib.request.urlopen

        def boom(request, timeout=0):
            raise OSError("connection refused")

        urllib.request.urlopen = boom
        try:
            self.assertFalse(self.module.check_mirror_reachable("https://mirror.example/simple"))
        finally:
            urllib.request.urlopen = original
        self.assertIn("镜像源不可用", self.logger.text())


class InstallPipConfigTemplateTests(unittest.TestCase):
    """tools/install.py 必须把默认关闭 pip 安装的模板放进包内。"""

    def test_install_pip_config_writes_disabled_template(self):
        spec = importlib.util.spec_from_file_location(
            "tools_install_under_test", REPO_ROOT / "tools" / "install.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        saved_cwd = os.getcwd()
        try:
            spec.loader.exec_module(module)
        finally:
            os.chdir(saved_cwd)

        with tempfile.TemporaryDirectory() as tmp:
            original = module.install_path
            module.install_path = Path(tmp)
            try:
                module.install_pip_config()
                written = json.loads(
                    (Path(tmp) / "config" / "pip_config.json").read_text(encoding="utf-8")
                )
            finally:
                module.install_path = original

        self.assertFalse(written["enable_pip_install"], "包内默认必须关闭运行时 pip 安装")
        self.assertTrue(written["mirror"])


class ImportRobustnessTests(unittest.TestCase):
    """pythonw / 被重定向的 stdout 没有 reconfigure，导入 agent/main.py 不能崩。"""

    def test_import_survives_stream_without_reconfigure(self):
        code = (
            "import io, runpy, sys\n"
            "sys.stdout = io.StringIO()\n"
            "sys.stderr = io.StringIO()\n"
            f"runpy.run_path({str(REPO_ROOT / 'agent' / 'main.py')!r}, run_name='agent_main_probe')\n"
            "sys.__stdout__.write('IMPORT_OK\\n')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IMPORT_OK", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
