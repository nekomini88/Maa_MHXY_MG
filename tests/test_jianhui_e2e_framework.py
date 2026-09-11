# -*- coding: utf-8 -*-
"""剑会「开始匹配」点击链路的端到端体检（有 MaaFw 才跑，否则跳过）。

单测里的 FakeReco 是我们自己写的假对象，证明不了「框架实际会把 Click 落在
框内哪一点」。这个文件跑 tools/dev/jianhui_e2e_check.py：合成弹窗截图 +
MaaFw 的 CustomController 冒充设备（screencap 返回合成图、记录触点坐标），
走仓库真实的 pipeline 与识别器，断言点击坐标落在按钮矩形内。

为什么用子进程：同一次 pytest 里 `tests/test_jianhui_pipei.py` 会往
`sys.modules` 塞假的 `maa` 包（`__path__` 为空），进程内再导入真框架必然失败。
子进程跑还顺带验证了脚本本身能被独立执行。

本地/服务器装了 MaaFw 的环境：
    /root/.venv-maacheck/bin/python -m pytest tests/test_jianhui_e2e_framework.py -q
CI 没装 MaaFw，本文件自动跳过。
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_PATH = REPO_ROOT / "tools" / "dev" / "jianhui_e2e_check.py"
RESULT_PREFIX = "RESULT_JSON:"
WAIT_SECONDS = 8.0

try:  # pragma: no cover - 取决于环境是否装了 MaaFw
    import maa  # noqa: F401

    HAS_MAAFW = True
except Exception:  # pragma: no cover
    HAS_MAAFW = False


@unittest.skipUnless(HAS_MAAFW, "需要 MaaFw（pip install MaaFw）才能做端到端空跑")
class JianhuiClickE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = subprocess.run(
            [sys.executable, str(HARNESS_PATH), "--wait", str(WAIT_SECONDS), "--json"],
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        cls.stdout = proc.stdout
        cls.stderr = proc.stderr
        cls.raw = next(
            (line[len(RESULT_PREFIX) :] for line in proc.stdout.splitlines() if line.startswith(RESULT_PREFIX)),
            None,
        )
        cls.result = json.loads(cls.raw) if cls.raw else None

    def _cases(self):
        self.assertIsNotNone(
            self.result,
            f"端到端脚本没输出结果行\nstdout:\n{self.stdout[-2000:]}\nstderr:\n{self.stderr[-2000:]}",
        )
        result = self.result or {}
        return {c["name"]: c for c in result["cases"]}

    def _assert_case_ok(self, name, expect_clicks):
        case = self._cases()[name]
        self.assertTrue(case["ok"], f"{name} 未通过：{case}")
        if expect_clicks:
            self.assertTrue(case["clicks"], f"{name} 一次都没点击")
            for point in case["clicks"]:
                bx, by, bw, bh = case["button"]
                with self.subTest(point=point):
                    self.assertTrue(
                        bx <= point[0] <= bx + bw and by <= point[1] <= by + bh,
                        f"{name} 点击{point}不在按钮{case['button']}内",
                    )
        else:
            self.assertEqual(case["clicks"], [], f"{name} 不该有任何点击")

    def test_landscape_clicks_inside_button(self):
        self._assert_case_ok("横屏 1280x720 · 含按钮", expect_clicks=True)

    def test_portrait_clicks_inside_button(self):
        self._assert_case_ok("竖屏 720x1280 · 含按钮", expect_clicks=True)

    def test_decoy_title_is_never_clicked(self):
        self._assert_case_ok("横屏 1280x720 · 只有诱饵标题", expect_clicks=False)


if __name__ == "__main__":
    unittest.main()
