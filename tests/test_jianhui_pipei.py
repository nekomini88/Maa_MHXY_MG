# -*- coding: utf-8 -*-
"""剑会匹配「点完不结束、一直保持」的结构与行为体检。

历史事故：`剑会-开始匹配` 节点点完没有 `next`，pipeline 到那里自然收敛，
MFA 打出「任务已全部完成！」——而用户要的是点完继续守着，弹窗再出现
（队友取消、匹配失败重试、打完一局回房间）就再点一次。这个文件兜两件事：

1. pipeline 结构：`剑会-保持匹配` 必须自循环（`next` 与 `on_error` 双保险）
   且 `timeout` 为 -1；链上不能有会终止任务的死枝，也不能有悬空引用
   （引用了不存在的节点会让 pipeline 静默结束）。
2. 识别器行为：命中返回框、等不到按钮不抛异常并按间隔轮询、请求停止时
   立刻退出、两次命中之间留冷却、截图异常不炸任务。
"""

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_PATH = (
    REPO_ROOT / "assets" / "resource" / "base" / "pipeline" / "jianhui_pipei.json"
)
JIANHUI_PATH = REPO_ROOT / "agent" / "custom" / "recognition" / "jianhui_pipei.py"

ENTRY_NODE = "jianhui_pipei"
KEEP_NODE = "剑会-保持匹配"


class FakeTime:
    """可推进的假时钟：sleep 直接推进 now，避免测试真的等。"""

    def __init__(self, start=1000.0):
        self.now = start

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(float(seconds), 0.0)


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


class FakeRect:
    """maafw 绑定里的 ``Rect`` dataclass（x, y, w, h）——不是 tuple，别按下标取。"""

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


class FakeOcrItem:
    def __init__(self, text, box=(10, 10, 100, 20), score=0.9):
        self.text = text
        self.box = box
        self.score = score


class FakeReco:
    """假的 ``RecognitionDetail``（字段名与 maafw 绑定一致）。"""

    def __init__(self, items=None, raw_only=False):
        items = items or []
        self.all_results = [] if raw_only else list(items)
        self.filtered_results = [] if raw_only else list(items)
        self.best_result = None if raw_only or not items else items[0]
        self.hit = bool(items)
        self.box = items[0].box if items else None
        if raw_only:
            # 老版本绑定：只有 raw_detail 里的 JSON，没有解析好的 dataclass。
            self.raw_detail = {
                "filtered": [
                    {"text": it.text, "box": _as_box_list(it.box), "score": it.score}
                    for it in items
                ],
                "all": [],
                "best": None,
            }
        else:
            self.raw_detail = {}


def _as_box_list(box):
    if isinstance(box, FakeRect):
        return [box.x, box.y, box.w, box.h]
    return list(box)


class FakeController:
    def __init__(self):
        self.error = None
        self.count = 0
        self.shape_image = _FakeImage()

    def post_screencap(self):
        self.count += 1
        if self.error is not None:
            return FakeJob(error=self.error)
        return FakeJob(value=self.shape_image)


class _FakeImage:
    """只提供 shape 的假帧（构造 ROI 用）。"""

    shape = (607, 1080, 3)


class FakeTasker:
    def __init__(self):
        self.controller = FakeController()
        self.stopping = False


def _mk_ocr_items(raw):
    """把测试里的简写（str / (text, box) / (text, box, score)）统一成候选对象。"""
    items = []
    for it in raw or []:
        if isinstance(it, FakeOcrItem):
            items.append(it)
        elif isinstance(it, str):
            items.append(FakeOcrItem(it))
        else:
            items.append(FakeOcrItem(*it))
    return items


class FakeContext:
    def __init__(self, ocr_items=None, reco_error=None, raw_only=False):
        self.tasker = FakeTasker()
        self.ocr_items = ocr_items
        self.reco_error = reco_error
        self.raw_only = raw_only
        self.recognition_calls = 0
        self.last_override = None

    def run_recognition(self, name, image, pipeline_override=None):
        self.recognition_calls += 1
        self.last_override = pipeline_override
        if self.reco_error is not None:
            raise self.reco_error
        return FakeReco(_mk_ocr_items(self.ocr_items), raw_only=self.raw_only)


class AnalyzeArgStub:
    custom_recognition_param = "{}"


def _install_stubs():
    """注册 maa / utils 桩模块；同进程里已被别的测试装过就直接复用。"""
    utils = sys.modules.get("utils")
    if utils is not None and hasattr(utils, "logger") and hasattr(utils, "send_message"):
        return utils

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
        AnalyzeResult = _Result
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

    class _Logger:
        def info(self, msg):
            return None

        def warning(self, msg):
            return None

    utils = types.ModuleType("utils")
    utils.__path__ = []
    utils.logger = _Logger()
    utils.send_message = lambda *a, **k: True
    sys.modules["utils"] = utils
    return utils


class _Result:
    def __init__(self, box=None, detail=""):
        self.box = box
        self.detail = detail


def load_jianhui():
    _install_stubs()
    module_name = "jianhui_pipei_under_test"
    if module_name in sys.modules:
        module = sys.modules[module_name]
    else:
        spec = importlib.util.spec_from_file_location(module_name, JIANHUI_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    module.time = FakeTime()
    return module


jianhui_mod = load_jianhui()


def make_arg(**params):
    arg = AnalyzeArgStub()
    arg.custom_recognition_param = json.dumps(params, ensure_ascii=False)
    return arg


class PipelineStructureTest(unittest.TestCase):
    """pipeline 层面：点完不能收敛，链上不能有死枝。"""

    @classmethod
    def setUpClass(cls):
        cls.pipe = json.loads(PIPELINE_PATH.read_text(encoding="utf-8"))

    def test_only_entry_and_keep_node_exist(self):
        # 多余节点 = 没人引用的死代码，用户对残留零容忍。
        self.assertEqual(set(self.pipe), {ENTRY_NODE, KEEP_NODE})

    def test_entry_never_times_out_and_only_goes_to_keep_node(self):
        entry = self.pipe[ENTRY_NODE]
        self.assertEqual(entry["timeout"], -1)
        self.assertEqual(entry["next"], [KEEP_NODE])

    def test_keep_node_self_loops_on_next_and_error(self):
        keep = self.pipe[KEEP_NODE]
        jump_back = f"[JumpBack]{KEEP_NODE}"
        # 命中点击后：next 自跳回本节点，等下一次弹窗。
        self.assertEqual(keep["next"], [jump_back])
        # 单轮没等到按钮：on_error 自跳回本节点，不算失败、不结束任务。
        self.assertEqual(keep["on_error"], [jump_back])
        self.assertEqual(keep["timeout"], -1)

    def test_keep_node_recognizes_with_custom_reco_and_clicks(self):
        keep = self.pipe[KEEP_NODE]
        self.assertEqual(keep["recognition"], "Custom")
        self.assertEqual(keep["custom_recognition"], "jianhui_pipei")
        self.assertEqual(keep["action"], "Click")
        self.assertGreater(keep["post_delay"], 0)

    def test_no_dangling_node_reference(self):
        # 引用不存在的节点会让 pipeline 静默结束——正是这次要修的毛病。
        for name, node in self.pipe.items():
            for key in ("next", "on_error", "interrupt"):
                for ref in node.get(key, []):
                    target = ref.replace("[JumpBack]", "").replace("[Anchor]", "")
                    with self.subTest(node=name, key=key, ref=ref):
                        self.assertIn(target, self.pipe)

    def test_keep_node_is_reachable(self):
        self.assertIn(KEEP_NODE, self.pipe[ENTRY_NODE]["next"])


class JianhuiPipeiBehaviorTest(unittest.TestCase):
    def setUp(self):
        jianhui_mod.time = FakeTime()
        jianhui_mod.JianhuiPipei._LAST_HIT_TS = 0.0
        jianhui_mod.JianhuiPipei._LAST_NOTIFY_TS = 0.0

    def analyze(self, context, **params):
        params.setdefault("jianhui_roi", [10, 10, 100, 40])
        params.setdefault("jianhui_interval", 0.001)
        return jianhui_mod.JianhuiPipei().analyze(context, make_arg(**params))

    def test_hit_returns_box_for_click(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        result = self.analyze(ctx)
        self.assertIsNotNone(result.box)
        self.assertEqual(result.box, [10, 10, 100, 20])
        self.assertIn("命中按钮", result.detail)

    def test_default_scan_is_whole_screen_with_anchored_pattern(self):
        # 分辨率/横竖屏无关 + 关键词必须锚定：这两条是「点不到按钮」的根治点。
        ctx = FakeContext(ocr_items=["开始匹配"])
        self.analyze(ctx, jianhui_roi=None)
        override = (ctx.last_override or {})[jianhui_mod.JianhuiPipei._ROC_NAME]
        self.assertEqual(override["roi"], [0, 0, 0, 0])
        self.assertEqual(jianhui_mod.DEFAULT_EXPECTED, [r"^\s*开始匹配\s*$"])
        self.assertEqual(override["expected"], [r"^\s*开始匹配\s*$"])

    def test_round_timeout_keeps_polling_and_does_not_raise(self):
        ctx = FakeContext(ocr_items=[])
        result = self.analyze(ctx, jianhui_max_wait=0.005)
        self.assertIsNone(result.box)
        self.assertIn("继续守候", result.detail)
        # 一轮里要反复截图重试，而不是一次没看到就放弃。
        self.assertGreater(ctx.recognition_calls, 1)

    def test_stopping_request_exits_immediately(self):
        ctx = FakeContext(ocr_items=[])
        ctx.tasker.stopping = True
        result = self.analyze(ctx, jianhui_max_wait=300)
        self.assertIsNone(result.box)
        self.assertIn("停止", result.detail)
        # 停止时不截图、不等待，最长一个轮询点内退出。
        self.assertLessEqual(ctx.tasker.controller.count, 1)

    def test_click_cooldown_spaces_out_consecutive_hits(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        first = self.analyze(ctx)
        self.assertIsNotNone(first.box)
        before = jianhui_mod.time.now
        second = self.analyze(ctx, jianhui_click_cooldown=0.5)
        self.assertIsNotNone(second.box)
        # 同一个按钮不会在同一瞬间被连点两下。
        self.assertGreaterEqual(jianhui_mod.time.now - before, 0.5)

    def test_click_cooldown_state_survives_new_instances(self):
        # analyze 每次都可能是新实例：状态写在实例上会被丢掉，冷却形同不存在。
        ctx = FakeContext(ocr_items=["开始匹配"])
        self.analyze(ctx)
        self.assertGreater(jianhui_mod.JianhuiPipei._LAST_HIT_TS, 0.0)

    def test_notify_is_throttled_within_window(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        sent = []
        original = jianhui_mod.send_message
        jianhui_mod.send_message = lambda *a, **k: (sent.append(a), True)[1]
        try:
            for _ in range(3):
                self.analyze(ctx, jianhui_notify=True, jianhui_click_cooldown=0.0)
        finally:
            jianhui_mod.send_message = original
        self.assertEqual(len(sent), 1)

    def test_screenshot_error_is_tolerated(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        ctx.tasker.controller.error = RuntimeError("adb 掉线")
        result = self.analyze(ctx, jianhui_max_wait=0.005)
        self.assertIsNone(result.box)
        self.assertIn("继续守候", result.detail)

    def test_ocr_error_is_tolerated(self):
        ctx = FakeContext(reco_error=RuntimeError("OCR 崩了"))
        result = self.analyze(ctx, jianhui_max_wait=0.005)
        self.assertIsNone(result.box)
        self.assertIn("继续守候", result.detail)

    def test_initial_screencap_failure_returns_none(self):
        # 只有显式要求按比例算 ROI 时才需要先截图；截图失败按轮询继续，不炸任务。
        ctx = FakeContext(ocr_items=["开始匹配"], reco_error=None)
        ctx.tasker.controller.error = RuntimeError("adb 掉线")
        arg = make_arg(jianhui_roi_ratio=[0.27, 0.18, 0.48, 0.50], jianhui_max_wait=0.005)
        result = jianhui_mod.JianhuiPipei().analyze(ctx, arg)
        self.assertIsNone(result.box)
        self.assertIn("继续守候", result.detail)

    # ---- 回归：v0.1.8 真机上「识别命中却点不到按钮」的根因 ----
    # 框架 OCR 的 expected 是子串正则、cherry_pick 默认取最靠左的框，
    # 于是弹窗标题「请及时开始匹配」/说明行「点击开始匹配进入对局」都会胜出，
    # Click 点在文字上，按钮一次都没被点到。

    DECOY_ITEMS = [
        ("请及时开始匹配", FakeRect(381, 205, 224, 26), 0.99),
        ("点击开始匹配进入对局", FakeRect(392, 243, 300, 24), 0.98),
        ("开始匹配", FakeRect(789, 412, 108, 38), 0.95),
    ]

    def test_picks_button_not_decoy_title(self):
        ctx = FakeContext(ocr_items=list(self.DECOY_ITEMS))
        result = self.analyze(ctx)
        self.assertEqual(result.box, [789, 412, 108, 38])
        self.assertIn("开始匹配", result.detail)

    def test_decoy_title_alone_never_clicked(self):
        items = [it for it in self.DECOY_ITEMS if it[0] != "开始匹配"]
        ctx = FakeContext(ocr_items=items)
        result = self.analyze(ctx, jianhui_max_wait=0.005)
        self.assertIsNone(result.box)
        self.assertNotIn("命中按钮", result.detail)

    def test_blocklisted_text_with_keyword_is_skipped(self):
        # 「我再等等」这类含黑名单字样的文本即使拿到候选也不点。
        ctx = FakeContext(
            ocr_items=[("请及时开始匹配，也可点击我再等等", FakeRect(300, 400, 400, 30), 0.99)]
        )
        result = self.analyze(ctx, jianhui_max_wait=0.005)
        self.assertIsNone(result.box)

    def test_rect_dataclass_candidates_are_parsed(self):
        # 绑定给的是 Rect(x, y, w, h) dataclass，不是 tuple，不能按下标取。
        ctx = FakeContext(ocr_items=[("开始匹配", FakeRect(11, 22, 33, 44), 0.9)])
        result = self.analyze(ctx)
        self.assertEqual(result.box, [11, 22, 33, 44])

    def test_button_text_with_spacing_or_punctuation_still_matches(self):
        for text in ("开始 匹配", "开始匹配·", " 开始匹配 "):
            with self.subTest(text=text):
                ctx = FakeContext(ocr_items=[(text, (1, 2, 3, 4), 0.9)])
                self.assertEqual(self.analyze(ctx).box, [1, 2, 3, 4])

    def test_candidates_present_but_not_button_never_falls_back(self):
        # 有候选却一个都不是按钮时不能退回 reco.box —— 那正是把标题当按钮点的坑。
        # FakeReco.box 此时正是诱饵标题的框，兜底一旦生效就会返回它。
        ctx = FakeContext(ocr_items=[("请及时开始匹配", FakeRect(381, 205, 224, 26), 0.99)])
        result = self.analyze(ctx, jianhui_max_wait=0.005)
        self.assertIsNone(result.box)

    def test_raw_detail_json_fallback_for_older_binding(self):
        # 老版本绑定没有 filtered_results，只有 raw_detail 里的 JSON。
        ctx = FakeContext(ocr_items=list(self.DECOY_ITEMS), raw_only=True)
        result = self.analyze(ctx)
        self.assertEqual(result.box, [789, 412, 108, 38])

    def test_falls_back_to_framework_box_when_no_candidate_detail(self):
        # 绑定没给候选明细时退回 reco.box —— 此时锚定正则已保证框架侧的
        # best 不会是标题，兜底是安全的。
        reco = FakeReco(items=None)
        reco.hit = True
        reco.box = FakeRect(5, 6, 7, 8)
        box, why = jianhui_mod.pick_button_box(reco)
        self.assertEqual(box, [5, 6, 7, 8])
        self.assertIn("兜底", why)

    def test_no_candidate_and_no_box_yields_nothing(self):
        reco = FakeReco(items=None)
        reco.hit = True
        box, why = jianhui_mod.pick_button_box(reco)
        self.assertIsNone(box)
        self.assertIn("无候选框", why)

    def test_disabled_short_circuits(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        result = self.analyze(ctx, jianhui_enabled=False)
        self.assertIsNone(result.box)
        self.assertIn("已禁用", result.detail)
        self.assertEqual(ctx.tasker.controller.count, 0)


if __name__ == "__main__":
    unittest.main()
