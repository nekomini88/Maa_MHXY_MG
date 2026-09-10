# -*- coding: utf-8 -*-
"""tools/ci/package.py 的单元测试：包体布局与 install/deps 排除。"""

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


class PackageBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module("tools/ci/package.py", "package_builder_under_test")

    def _make_install_tree(self, root):
        root = Path(root) / "install"
        (root / "python" / "Lib" / "site-packages" / "numpy").mkdir(parents=True)
        (root / "python" / "Lib" / "site-packages" / "numpy" / "__init__.py").write_text("x")
        (root / "python" / "python.exe").write_text("x")
        (root / "agent").mkdir()
        (root / "agent" / "main.py").write_text("x")
        (root / "config").mkdir()
        (root / "config" / "pip_config.json").write_text("{}")
        (root / "interface.json").write_text("{}")
        # 需要被排除的离线 wheel 仓库
        (root / "deps").mkdir()
        (root / "deps" / "scipy-1.18.1-cp312-cp312-win_amd64.whl").write_text("x" * 1000)
        return Path(root).parent

    def test_excludes_wheel_repository_and_keeps_runtime_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = self._make_install_tree(tmp)
            out = Path(tmp) / "pkg.zip"
            count, size = self.module.build(str(staging / "install"), str(out))
            with zipfile.ZipFile(out) as archive:
                names = archive.namelist()

        self.assertGreater(count, 0)
        self.assertGreater(size, 0)
        self.assertNotIn("deps/scipy-1.18.1-cp312-cp312-win_amd64.whl", names)
        self.assertFalse([n for n in names if n.startswith("deps/")], "install/deps 不应进包")
        self.assertIn("python/python.exe", names)
        self.assertIn("python/Lib/site-packages/numpy/__init__.py", names)
        self.assertIn("agent/main.py", names)
        self.assertIn("config/pip_config.json", names)
        self.assertIn("interface.json", names)

    def test_zip_layout_is_relative_to_install_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = self._make_install_tree(tmp)
            out = Path(tmp) / "pkg.zip"
            self.module.build(str(staging / "install"), str(out))
            with zipfile.ZipFile(out) as archive:
                names = archive.namelist()
        self.assertFalse([n for n in names if n.startswith("install/")], "arcname 不应带 install/ 前缀")

    def test_missing_install_dir_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                self.module.build(os.path.join(tmp, "nope"), os.path.join(tmp, "out.zip"))

    def test_cli_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = self._make_install_tree(tmp)
            out = Path(tmp) / "pkg.zip"
            code = self.module.main(["package.py", str(staging / "install"), str(out)])
            self.assertEqual(code, 0)
            self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
