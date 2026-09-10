# -*- coding: utf-8 -*-
"""agent/ 全部源码的语法与相对导入体检。

历史事故：`agent/custom/recognition/__init__.py` 里两条 import 被拼接成一行
（`from .jingjichang import *from .jianhui_pipei import *`），SyntaxError 让
`import custom` 直接崩，表现为 MFA 只报一句 "agent 运行过程中发生异常"、日志里
看不到回溯。这个文件专门兜住这一类问题——任何 .py 语法错误都必须在发版前拦住。
"""

import ast
import compileall
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = ("agent", "tools", "tests")


def iter_python_files():
    for directory in SOURCE_DIRS:
        root = REPO_ROOT / directory
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


class AgentSourceSyntaxTests(unittest.TestCase):
    def test_all_agent_sources_compile(self):
        broken = []
        for path in iter_python_files():
            source = path.read_text(encoding="utf-8")
            try:
                compile(source, str(path), "exec")
            except SyntaxError as exc:
                broken.append(f"{path.relative_to(REPO_ROOT)}:{exc.lineno}: {exc.msg}")
        self.assertEqual(broken, [], "以下文件存在语法错误:\n" + "\n".join(broken))

    def test_compileall_is_clean(self):
        """用 compileall 再扫一遍（覆盖 compile() 之外的编码等边角情况）。"""
        for directory in SOURCE_DIRS:
            root = REPO_ROOT / directory
            if not root.is_dir():
                continue
            self.assertTrue(
                compileall.compile_dir(str(root), quiet=2, force=True),
                f"{directory}/ 下存在无法编译的文件",
            )

    def test_recognition_package_import_targets_exist(self):
        """custom/recognition/__init__.py 里每条 `from .X import` 都要有对应文件。"""
        package = REPO_ROOT / "agent" / "custom" / "recognition"
        init_file = package / "__init__.py"
        self.assertTrue(init_file.exists())

        tree = ast.parse(init_file.read_text(encoding="utf-8"))
        missing = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                target = package / f"{node.module}.py"
                target_package = package / node.module / "__init__.py"
                if not target.exists() and not target_package.exists():
                    missing.append(node.module)
        self.assertEqual(missing, [], f"recognition/__init__.py 引用了不存在的模块: {missing}")

    def test_recognition_imports_are_one_per_line(self):
        """回归：两条 import 被拼到同一行会直接 SyntaxError。"""
        init_file = REPO_ROOT / "agent" / "custom" / "recognition" / "__init__.py"
        for lineno, line in enumerate(init_file.read_text(encoding="utf-8").splitlines(), 1):
            if line.count("import") > 1:
                count = line.count("import")
                self.fail(f"{init_file.name}:{lineno} 一行里出现 {count} 个 import: {line!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
