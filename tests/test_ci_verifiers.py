# -*- coding: utf-8 -*-
"""CI 门禁脚本的单元测试：verify_embed_deps / verify_package。

这两个脚本是防止「包内没带预装依赖」再次流到用户手里的最后一道闸门，
所以它们自己必须被测试。
"""

import importlib.util
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_module(relative_path, name):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class VerifyEmbedDepsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module("tools/ci/verify_embed_deps.py", "verify_embed_deps_under_test")

    def _make_site_packages(self, root, packages=None, dist_count=30):
        target = Path(root) / "site-packages"
        target.mkdir(parents=True)
        for package in packages if packages is not None else self.module.REQUIRED_PACKAGES:
            (target / package).mkdir()
        for index in range(dist_count):
            (target / f"pkg{index}-1.0.dist-info").mkdir()
        return target

    def test_passes_on_complete_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_site_packages(tmp)
            ok, message = self.module.verify(str(target))
        self.assertTrue(ok, message)
        self.assertIn("dist-info", message)

    def test_fails_when_package_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages = [p for p in self.module.REQUIRED_PACKAGES if p != "cv2"]
            target = self._make_site_packages(tmp, packages=packages)
            ok, message = self.module.verify(str(target))
        self.assertFalse(ok)
        self.assertIn("cv2", message)

    def test_fails_when_dist_info_too_few(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_site_packages(tmp, dist_count=3)
            ok, message = self.module.verify(str(target))
        self.assertFalse(ok)
        self.assertIn("dist-info 数量不足", message)

    def test_fails_when_directory_absent(self):
        ok, message = self.module.verify("/nonexistent/site-packages")
        self.assertFalse(ok)
        self.assertIn("目录不存在", message)

    def test_main_returns_nonzero_on_failure(self):
        code = self.module.main(["verify_embed_deps.py", "/nonexistent/site-packages"])
        self.assertEqual(code, 1)


class VerifyPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module("tools/ci/verify_package.py", "verify_package_under_test")

    def _build_zip(self, path, entries):
        with zipfile.ZipFile(path, "w") as archive:
            for entry in entries:
                archive.writestr(entry, "x")
        return path

    def _complete_entries(self):
        return [
            "python/python.exe",
            "python/Lib/site-packages/numpy/__init__.py",
            "python/Lib/site-packages/cv2/__init__.py",
            "python/Lib/site-packages/maa/__init__.py",
            "python/Lib/site-packages/PIL/__init__.py",
            "agent/main.py",
            "config/pip_config.json",
            "interface.json",
        ]

    def test_passes_on_complete_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._build_zip(os.path.join(tmp, "ok.zip"), self._complete_entries())
            ok, message = self.module.verify(path)
        self.assertTrue(ok, message)

    def test_fails_without_embedded_site_packages(self):
        """回归：曾经发出去的包只有 python/ 没有 python/Lib/site-packages。"""
        entries = [e for e in self._complete_entries() if "site-packages" not in e]
        with tempfile.TemporaryDirectory() as tmp:
            path = self._build_zip(os.path.join(tmp, "bad.zip"), entries)
            ok, message = self.module.verify(path)
        self.assertFalse(ok)
        self.assertIn("site-packages", message)

    def test_fails_without_pip_config(self):
        entries = [e for e in self._complete_entries() if e != "config/pip_config.json"]
        with tempfile.TemporaryDirectory() as tmp:
            path = self._build_zip(os.path.join(tmp, "bad2.zip"), entries)
            ok, message = self.module.verify(path)
        self.assertFalse(ok)
        self.assertIn("pip_config.json", message)

    def test_fails_on_corrupt_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corrupt.zip"
            path.write_text("not a zip")
            ok, message = self.module.verify(str(path))
        self.assertFalse(ok)
        self.assertIn("无法读取 zip", message)

    def test_main_requires_argument(self):
        self.assertEqual(self.module.main(["verify_package.py"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
