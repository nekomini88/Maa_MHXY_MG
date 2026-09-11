# -*- coding: utf-8 -*-
"""擂台/竞技场/剑会「开始匹配」点击链路的端到端体检（有 MaaFw 才跑，否则跳过）。

单测里的 FakeReco 是我们自己写的假对象，证明不了「框架实际会把 Click 落在
框内哪一点」。这个文件跑 tools/dev/button_e2e_check.py：按任务合成面板截图 +
MaaFw 的 CustomController 冒充设备（screencap 返回合成图、记录触点坐标），
走仓库真实的 pipeline 与识别器，断言每一次点击都落在按钮矩形内、只有诱饵
文字时零点击。

为什么用子进程：同一次 pytest 里 `tests/test_*.py` 会往 `sys.modules`
塞假的 `maa` 包（`__path__` 为空），进程内再导入真框架必然失败。子进程跑
还顺带验证了脚本本身能被独立执行。

本地/服务器装了 MaaFw 的环境：
    /root/.venv-maacheck/bin/python -m pytest tests/test_button_e2e_framework.py -q
CI 没装 MaaFw，本文件自动跳过。
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_PATH = REPO_ROOT / "tools" / "dev" / "button_e2e_check.py"
RESULT_PREFIX = "RESULT_JSON:"
WAIT_SECONDS = 8.0

try:  # pragma: no cover - 取决于环境是否装了 MaaFw
    import maa  # noqa: F401

    HAS_MAAFW = True
except Exception:  # pragma: no cover
    HAS_MAAFW = False


def _run_harness(task):
    proc = subprocess.run(
        [
            sys.executable,
            str(HARNESS_PATH),
            "--task",
            task,
            "--wait",
            str(WAIT_SECONDS),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    raw = next(
        (
            line[len(RESULT_PREFIX) :]
            for line in proc.stdout.splitlines()
            if line.startswith(RESULT_PREFIX)
        ),
        None,
    )
    return proc, (json.loads(raw) if raw else None)


@unittest.skipUnless(HAS_MAAFW, "需要 MaaFw（pip install MaaFw）才能做端到端空跑")
class ButtonClickE2ETest(unittest.TestCase):
    """三个任务各跑一次空跑脚本（各一个子进程，好定位失败的是哪个任务）。"""

    TASKS = ("jianhui", "leitai", "jingjichang")

    @classmethod
    def setUpClass(cls):
        cls.results = {}
        cls.outputs = {}
        for task in cls.TASKS:
            proc, result = _run_harness(task)
            cls.outputs[task] = f"stdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-2000:]}"
            cls.results[task] = result

    def _cases(self, task):
        result = self.results.get(task)
        self.assertIsNotNone(result, f"{task} 端到端脚本没输出结果行\n{self.outputs.get(task)}")
        return {c["name"]: c for c in result["cases"]}

    def _assert_case_ok(self, task, name, expect_clicks):
        case = self._cases(task)[name]
        self.assertTrue(case["ok"], f"[{task}] {name} 未通过：{case}")
        if expect_clicks:
            self.assertTrue(case["clicks"], f"[{task}] {name} 一次都没点击")
            for point in case["clicks"]:
                bx, by, bw, bh = case["button"]
                with self.subTest(task=task, point=point):
                    self.assertTrue(
                        bx <= point[0] <= bx + bw and by <= point[1] <= by + bh,
                        f"[{task}] {name} 点击{point}不在按钮{case['button']}内",
                    )
        else:
            self.assertEqual(case["clicks"], [], f"[{task}] {name} 不该有任何点击")

    def test_landscape_clicks_inside_button(self):
        for task in self.TASKS:
            with self.subTest(task=task):
                self._assert_case_ok(task, "横屏 1280x720 · 含按钮", expect_clicks=True)

    def test_portrait_clicks_inside_button(self):
        for task in self.TASKS:
            with self.subTest(task=task):
                self._assert_case_ok(task, "竖屏 720x1280 · 含按钮", expect_clicks=True)

    def test_decoy_text_is_never_clicked(self):
        for task in self.TASKS:
            with self.subTest(task=task):
                self._assert_case_ok(task, "横屏 1280x720 · 只有诱饵文字", expect_clicks=False)


if __name__ == "__main__":
    unittest.main()
