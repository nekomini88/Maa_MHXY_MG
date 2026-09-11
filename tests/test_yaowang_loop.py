# -*- coding: utf-8 -*-
"""蹲妖王整轮行为的端到端测试（打桩 maa / utils，在 Linux CI 上真跑 analyze）。

覆盖的是"总是跑一段时间就失败、不能一直监控"的修复：
  * 画面冻结 → 立刻告警 + 干净收尾（旧版会盲跑到扫描上限，实测 49 分钟）
  * 控制器断连 + 截图失败 → 判定断连并告警
  * 任务被请求停止 → 立刻退出当轮
  * 告警控频跨轮保留（同一个故障不刷屏）、恢复后补一条「已恢复」
  * 命中妖王仍正常通知（防回归）
"""

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = REPO_ROOT / "agent"
UTILS_DIR = AGENT_DIR / "utils"
YAOWANG_PATH = AGENT_DIR / "custom" / "recognition" / "yaowang.py"
LINK_GUARD_PATH = UTILS_DIR / "link_guard.py"


# --------------------------------------------------------------------------- 桩
class FakeTime:
    """确定性时钟：每次 sleep 推进 step 秒，避免测试依赖真实等待。"""

    def __init__(self, step=0.3):
        self.t = 1000.0
        self.step = step

    def time(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(self.step, float(seconds))


class FakeLog:
    def __init__(self):
        self.lines = []

    def _add(self, level, msg):
        self.lines.append((level, str(msg)))

    def info(self, msg):
        self._add("INFO", msg)

    def warning(self, msg):
        self._add("WARN", msg)

    def error(self, msg):
        self._add("ERROR", msg)

    def debug(self, msg):
        self._add("DEBUG", msg)

    def add(self, *args, **kwargs):
        return 1

    def text(self):
        return "\n".join(m for _, m in self.lines)


class FakeJob:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def wait(self):
        return self

    def get(self):
        if self.error:
            raise self.error
        return self.value


class FakeResult:
    def __init__(self, box=None, detail=""):
        self.box = box
        self.detail = detail


class FakeOcrItem:
    def __init__(self, text, box=(10, 10, 100, 20)):
        self.text = text
        self.box = box


class FakeReco:
    def __init__(self, items):
        self.hit = bool(items)
        self.all_results = [FakeOcrItem(t) for t in items]


class FakeController:
    def __init__(self):
        self.connected = True
        self.frames = []          # 依次返回的帧；空则重复最后一帧
        self.error = None         # 非空则截图抛异常
        self.count = 0
        self.reconnects = 0

    def _frame(self):
        if not self.frames:
            return np.zeros((40, 60, 3), dtype=np.uint8)
        if self.count < len(self.frames):
            frame = self.frames[self.count]
        else:
            frame = self.frames[-1]
        self.count += 1
        return frame

    def post_screencap(self):
        if self.error is not None:
            self.count += 1
            return FakeJob(error=self.error)
        return FakeJob(value=self._frame())

    def post_connection(self):
        self.reconnects += 1
        return FakeJob(value=None)


class FakeTasker:
    def __init__(self):
        self.controller = FakeController()
        self.stopping = False
        self.running = True


class FakeContext:
    def __init__(self, ocr_items=None):
        self.tasker = FakeTasker()
        self.ocr_items = ocr_items
        self.recognition_calls = 0

    def run_recognition(self, name, image, pipeline_override=None):
        self.recognition_calls += 1
        if self.ocr_items is None:
            return FakeReco([])
        return FakeReco(self.ocr_items)


class AnalyzeArgStub:
    """maa 的 AnalyzeArg 桩：analyze 只读 custom_recognition_param。"""

    custom_recognition_param = "{}"


def _install_stubs():
    """注册 maa / utils 桩模块（只在第一次调用时生效）。"""
    if "utils" in sys.modules and getattr(sys.modules["utils"], "__yaowang_stub__", False):
        return sys.modules["utils"]

    # --- maa ---
    maa = types.ModuleType("maa")
    maa.__path__ = []
    maa_agent = types.ModuleType("maa.agent")
    maa_agent.__path__ = []
    agent_server = types.ModuleType("maa.agent.agent_server")

    class _AgentServer:
        @staticmethod
        def custom_recognition(name):
            def deco(cls):
                cls.__recognition_name__ = name
                return cls

            return deco

    agent_server.AgentServer = _AgentServer

    custom_recognition = types.ModuleType("maa.custom_recognition")

    class _CustomRecognition:
        AnalyzeResult = FakeResult
        AnalyzeArg = AnalyzeArgStub

        def analyze(self, context, argv):  # pragma: no cover - 抽象方法
            raise NotImplementedError

    custom_recognition.CustomRecognition = _CustomRecognition
    context_mod = types.ModuleType("maa.context")
    context_mod.Context = object

    for name, mod in (
        ("maa", maa),
        ("maa.agent", maa_agent),
        ("maa.agent.agent_server", agent_server),
        ("maa.custom_recognition", custom_recognition),
        ("maa.context", context_mod),
    ):
        sys.modules[name] = mod

    # --- utils ---
    fake_log = FakeLog()
    utils = types.ModuleType("utils")
    utils.__path__ = []
    utils.__yaowang_stub__ = True
    utils.logger = fake_log

    log_policy = types.ModuleType("utils.log_policy")
    log_policy.sink_kwargs = lambda: {}
    log_policy.prune_old_logs = lambda *a, **k: None
    utils.log_policy = log_policy
    sys.modules["utils.log_policy"] = log_policy

    sent = []
    message = types.ModuleType("utils.message")
    message.config = {}
    message.read_config = lambda: {}
    utils.message = message
    sys.modules["utils.message"] = message

    def fake_send_message(title, content, *args, **kwargs):
        sent.append((title, content))
        return True

    utils.send_message = fake_send_message
    utils.sent = sent
    utils._fake_log = fake_log

    if str(UTILS_DIR) not in sys.path:
        sys.path.insert(0, str(UTILS_DIR))
    spec = importlib.util.spec_from_file_location("link_guard_real", LINK_GUARD_PATH)
    link_guard = importlib.util.module_from_spec(spec)
    sys.modules["link_guard_real"] = link_guard
    spec.loader.exec_module(link_guard)
    utils.link_guard = link_guard
    sys.modules["utils.link_guard"] = link_guard

    sys.modules["utils"] = utils
    return utils


def load_yaowang():
    utils = _install_stubs()
    spec = importlib.util.spec_from_file_location("yaowang_under_test", YAOWANG_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["yaowang_under_test"] = module
    spec.loader.exec_module(module)
    module.time = FakeTime()
    return module, utils


yaowang_mod, utils_stub = load_yaowang()


def make_arg(**params):
    arg = AnalyzeArgStub()
    arg.custom_recognition_param = json.dumps(params, ensure_ascii=False)
    return arg


def frame(level):
    """整帧单色：level 不同 → 帧差大于阈值（视为"有变化"）。"""
    return np.full((40, 60, 3), level, dtype=np.uint8)


class YaowangLoopTestCase(unittest.TestCase):
    def setUp(self):
        utils_stub.sent.clear()
        utils_stub._fake_log.lines.clear()
        yaowang_mod.Yaowang._LAST_FRAME = None
        yaowang_mod.Yaowang._LAST_NOTIFY_TS = 0.0
        yaowang_mod._LINK_GUARD = yaowang_mod.link_guard.LinkGuard()
        yaowang_mod.time = FakeTime()

    def analyze(self, context, **params):
        params.setdefault("yaowang_frame_interval", 0.001)
        params.setdefault("yaowang_rois", [[0, 0, 60, 40]])
        return yaowang_mod.Yaowang().analyze(context, make_arg(**params))


class NormalScanTest(YaowangLoopTestCase):
    def test_changing_screen_runs_to_scan_cap_without_alert(self):
        ctx = FakeContext()
        ctx.tasker.controller.frames = [frame(0), frame(200), frame(0), frame(200)]
        result = self.analyze(ctx, yaowang_max_scan=4)
        self.assertIsNone(result.box)
        self.assertIn("扫描上限", result.detail)
        self.assertEqual(utils_stub.sent, [])

    def test_yaowang_hit_still_notifies(self):
        ctx = FakeContext(ocr_items=["妖魔冲到了建邺城（25级可挑战），大家快去击败妖王啊！"])
        ctx.tasker.controller.frames = [frame(0), frame(200)]
        result = self.analyze(ctx, yaowang_max_scan=1)
        self.assertIsNotNone(result.box)
        self.assertEqual(len(utils_stub.sent), 1)
        self.assertEqual(utils_stub.sent[0][0], "妖王出现")
        self.assertIn("妖王", utils_stub.sent[0][1])

    def test_stopping_task_exits_immediately(self):
        ctx = FakeContext()
        ctx.tasker.stopping = True
        result = self.analyze(ctx, yaowang_max_scan=1000)
        self.assertEqual(result.detail, "任务已停止")
        self.assertEqual(ctx.tasker.controller.count, 0)  # 一次截图都不做


class FrozenLinkTest(YaowangLoopTestCase):
    def test_frozen_screen_alerts_and_ends_round_early(self):
        ctx = FakeContext()
        ctx.tasker.controller.frames = [frame(7)]  # 永远是同一帧
        result = self.analyze(ctx, yaowang_max_scan=500, yaowang_freeze_seconds=1)
        self.assertEqual(result.detail, "画面冻结，本轮告警收尾")
        self.assertLess(ctx.tasker.controller.count, 500, "不该盲跑到扫描上限")
        self.assertEqual(len(utils_stub.sent), 1)
        title, content = utils_stub.sent[0]
        self.assertEqual(title, "妖王监控异常")
        self.assertIn("冻结", content)
        self.assertIn("判定截图链路失效", utils_stub._fake_log.text())
        self.assertGreaterEqual(ctx.tasker.controller.reconnects, 1, "应主动尝试重连")

    def test_second_round_within_cooldown_does_not_alert_again(self):
        """同一个故障的后续各轮（画面一直没变）不该重复刷告警。"""
        ctx = FakeContext()
        ctx.tasker.controller.frames = [frame(7)]
        self.analyze(ctx, yaowang_max_scan=500, yaowang_freeze_seconds=1)
        self.assertEqual(len(utils_stub.sent), 1)

        utils_stub._fake_log.lines.clear()
        ctx2 = FakeContext()
        ctx2.tasker.controller.frames = [frame(7)]  # 与上一轮末帧相同 → 直接进入冻结判定
        result = self.analyze(ctx2, yaowang_max_scan=500, yaowang_freeze_seconds=1)
        self.assertEqual(result.detail, "画面冻结，本轮告警收尾")
        self.assertEqual(len(utils_stub.sent), 1, "冷却期内不应重复告警")
        self.assertIn("告警冷却期", utils_stub._fake_log.text())

    def test_recovery_notice_after_link_returns(self):
        ctx = FakeContext()
        ctx.tasker.controller.frames = [frame(7)]
        self.analyze(ctx, yaowang_max_scan=500, yaowang_freeze_seconds=1)
        self.assertEqual(len(utils_stub.sent), 1)

        ctx2 = FakeContext()
        ctx2.tasker.controller.frames = [frame(0), frame(200)] * 4  # 持续在刷新
        self.analyze(ctx2, yaowang_max_scan=8)
        self.assertEqual([t for t, _ in utils_stub.sent], ["妖王监控异常", "妖王监控已恢复"])
        self.assertIn("画面已恢复刷新", utils_stub.sent[1][1])

    def test_stale_first_frame_does_not_fake_recovery(self):
        """掉线时下一轮的第一帧必然"看起来变了"（帧差 inf），不能据此报「已恢复」。"""
        ctx = FakeContext()
        ctx.tasker.controller.frames = [frame(7)]
        self.analyze(ctx, yaowang_max_scan=500, yaowang_freeze_seconds=1)
        self.assertEqual([t for t, _ in utils_stub.sent], ["妖王监控异常"])

        ctx2 = FakeContext()
        # 先给一帧"变了"的旧画面，之后一直静止 → 仍然是掉线状态
        ctx2.tasker.controller.frames = [frame(9)] + [frame(9)] * 100
        result = self.analyze(ctx2, yaowang_max_scan=500, yaowang_freeze_seconds=1)
        self.assertEqual(result.detail, "画面冻结，本轮告警收尾")
        self.assertEqual([t for t, _ in utils_stub.sent], ["妖王监控异常"],
                         "误报「已恢复」会让用户以为监控还在跑")


class DisconnectedControllerTest(YaowangLoopTestCase):
    def test_screencap_error_plus_disconnected_alerts(self):
        ctx = FakeContext()
        ctx.tasker.controller.connected = False
        ctx.tasker.controller.error = RuntimeError("post_screencap 失败：设备未连接")
        result = self.analyze(ctx, yaowang_max_scan=500, yaowang_screencap_fail_limit=30)
        self.assertEqual(result.detail, "截图链路失效，本轮告警收尾")
        self.assertEqual(len(utils_stub.sent), 1)
        self.assertIn("断连", utils_stub.sent[0][1])
        self.assertLess(ctx.tasker.controller.count, 30, "断连+失败应早于失败次数上限判定")

    def test_screencap_errors_with_healthy_connection_keep_retrying(self):
        """只是偶发截图失败（连接正常）时不该误告警。"""
        ctx = FakeContext()
        ctx.tasker.controller.error = RuntimeError("偶发失败")
        result = self.analyze(ctx, yaowang_max_scan=6, yaowang_screencap_fail_limit=30)
        self.assertIn("扫描上限", result.detail)
        self.assertEqual(utils_stub.sent, [])


if __name__ == "__main__":
    unittest.main()
