# -*- coding: utf-8 -*-
"""擂台匹配与剑会同源的体检：结构常驻 + 锚定点击。

擂台和剑会犯的是同一个毛病：

* 识别关键词写成「包含匹配」，面板上的提示文字（「点击开始匹配，队伍满5人开战」）
  同样命中，而框架取**最靠左**的命中框，提示在按钮左边时 Click 就点在文字上；
* 固定比例 ROI 按一张横屏截图量的，换分辨率/竖屏整块错开，按钮反而落在扫描区外；
* 点完就走 `panduan_zhujiemian` 收任务，擂台要的是「有人退队→弹窗重现→再点」。

所以这里既校验 pipeline 的自循环结构，也校验识别器的锚定与守候行为。
假 maa / utils 与假设备在 `tests/maa_stubs.py`。
"""

import unittest

from maa_stubs import (
    FakeContext,
    FakeRect,
    all_pipeline_nodes,
    load_pipeline,
    load_recognizer,
    make_arg,
    node_refs,
)

PIPELINE = "leitai_pipei.json"
ENTRY_NODE = "leitai_pipei"
KEEP_NODE = "擂台匹配-保持匹配"

leitai_mod = load_recognizer("leitai_pipei.py")


class PipelineStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipe = load_pipeline(PIPELINE)
        cls.all_nodes = all_pipeline_nodes()

    def test_only_entry_and_keep_node_exist(self):
        self.assertEqual(set(self.pipe), {ENTRY_NODE, KEEP_NODE})

    def test_entry_never_times_out_and_only_goes_to_keep_node(self):
        entry = self.pipe[ENTRY_NODE]
        self.assertEqual(entry["timeout"], -1)
        self.assertEqual(entry["next"], [KEEP_NODE])

    def test_keep_node_self_loops_on_next_and_error(self):
        keep = self.pipe[KEEP_NODE]
        jump_back = f"[JumpBack]{KEEP_NODE}"
        self.assertEqual(keep["next"], [jump_back])
        self.assertEqual(keep["on_error"], [jump_back])
        self.assertEqual(keep["timeout"], -1)

    def test_keep_node_recognizes_with_custom_reco_and_clicks(self):
        keep = self.pipe[KEEP_NODE]
        self.assertEqual(keep["recognition"], "Custom")
        self.assertEqual(keep["custom_recognition"], "leitai_pipei")
        self.assertEqual(keep["action"], "Click")
        self.assertGreater(keep["post_delay"], 0)

    def test_task_never_ends_on_the_group_screen(self):
        # 旧结构点完就跳 panduan_zhujiemian 收任务；常驻后链上不能有这条出口。
        for name, key, target in node_refs(self.pipe):
            with self.subTest(node=name, key=key, ref=target):
                self.assertNotEqual(target, "panduan_zhujiemian")

    def test_no_dangling_node_reference(self):
        for name, key, target in node_refs(self.pipe):
            with self.subTest(node=name, key=key, ref=target):
                self.assertIn(target, self.all_nodes)


class LeitaiPipeiBehaviorTest(unittest.TestCase):
    def setUp(self):
        leitai_mod.time = type(leitai_mod.time)()
        leitai_mod.LeitaiPipei._LAST_HIT_TS = 0.0
        leitai_mod.LeitaiPipei._LAST_NOTIFY_TS = 0.0

    def analyze(self, context, **params):
        params.setdefault("leitai_roi", [10, 10, 100, 40])
        params.setdefault("leitai_interval", 0.001)
        return leitai_mod.LeitaiPipei().analyze(context, make_arg(**params))

    def test_hit_returns_box_for_click(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        result = self.analyze(ctx)
        self.assertEqual(result.box, [10, 10, 100, 20])
        self.assertIn("命中按钮", result.detail)

    def test_default_scan_is_whole_screen_with_anchored_pattern(self):
        # 分辨率/横竖屏无关 + 关键词锚定：这两条正是擂台点不到按钮的根治点。
        ctx = FakeContext(ocr_items=["开始匹配"])
        self.analyze(ctx, leitai_roi=None)
        override = (ctx.last_override or {})[leitai_mod.LeitaiPipei._ROC_NAME]
        self.assertEqual(override["roi"], [0, 0, 0, 0])
        self.assertEqual(leitai_mod.DEFAULT_EXPECTED, [r"^\s*开始匹配\s*$"])
        self.assertEqual(override["expected"], [r"^\s*开始匹配\s*$"])

    def test_round_timeout_keeps_polling_and_does_not_raise(self):
        ctx = FakeContext(ocr_items=[])
        result = self.analyze(ctx, leitai_max_wait=0.005)
        self.assertIsNone(result.box)
        self.assertIn("继续守候", result.detail)
        self.assertGreater(ctx.recognition_calls, 1)

    def test_stopping_request_exits_immediately(self):
        ctx = FakeContext(ocr_items=[])
        ctx.tasker.stopping = True
        result = self.analyze(ctx, leitai_max_wait=300)
        self.assertIsNone(result.box)
        self.assertIn("停止", result.detail)
        self.assertLessEqual(ctx.tasker.controller.count, 1)

    def test_repeated_hits_are_spaced_by_cooldown(self):
        # 常驻意味着反复命中同一个弹窗，没有冷却就会连点。
        ctx = FakeContext(ocr_items=["开始匹配"])
        self.analyze(ctx)
        before = leitai_mod.time.now
        self.analyze(ctx, leitai_click_cooldown=0.5)
        self.assertGreaterEqual(leitai_mod.time.now - before, 0.5)

    def test_cooldown_state_survives_new_instances(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        self.analyze(ctx)
        self.assertGreater(leitai_mod.LeitaiPipei._LAST_HIT_TS, 0.0)

    def test_notify_is_throttled(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        sent = []
        original = leitai_mod.send_message
        leitai_mod.send_message = lambda *a, **k: (sent.append(a), True)[1]
        try:
            for _ in range(3):
                self.analyze(ctx, leitai_notify=True, leitai_click_cooldown=0.0)
        finally:
            leitai_mod.send_message = original
        self.assertEqual(len(sent), 1)

    def test_screenshot_error_is_tolerated(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        ctx.tasker.controller.error = RuntimeError("adb 掉线")
        result = self.analyze(ctx, leitai_max_wait=0.005)
        self.assertIsNone(result.box)
        self.assertIn("继续守候", result.detail)

    def test_ocr_error_is_tolerated(self):
        ctx = FakeContext(reco_error=RuntimeError("OCR 崩了"))
        result = self.analyze(ctx, leitai_max_wait=0.005)
        self.assertIsNone(result.box)
        self.assertIn("继续守候", result.detail)

    def test_ratio_roi_is_used_when_explicitly_given(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        self.analyze(ctx, leitai_roi=None, leitai_roi_ratio=[0.48, 0.70, 0.32, 0.25])
        override = (ctx.last_override or {})[leitai_mod.LeitaiPipei._ROC_NAME]
        # 1080x607 的假帧 → 比例换算成绝对像素
        self.assertEqual(override["roi"], [518, 424, 345, 151])

    def test_disabled_short_circuits(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        result = self.analyze(ctx, leitai_enabled=False)
        self.assertIsNone(result.box)
        self.assertIn("已禁用", result.detail)
        self.assertEqual(ctx.tasker.controller.count, 0)

    # ---- 回归：擂台面板上的提示文字不是按钮 ----

    LEITAI_ITEMS = [
        # 右下按钮上方/左侧的说明文字：含「开始匹配」且更靠左，旧版就是点它。
        ("点击开始匹配，队伍满5人开战", FakeRect(520, 700, 300, 26), 0.98),
        # 左下提示含「匹配」但不含「开始匹配」，锚定后结构上不可能命中。
        ("队伍组满5人方可开始擂台乱斗匹配", FakeRect(60, 900, 420, 26), 0.97),
        ("开始匹配", FakeRect(760, 780, 120, 45), 0.95),
    ]

    def test_picks_the_button_not_the_hint_text(self):
        ctx = FakeContext(ocr_items=list(self.LEITAI_ITEMS))
        result = self.analyze(ctx)
        self.assertEqual(result.box, [760, 780, 120, 45])
        self.assertIn("开始匹配", result.detail)

    def test_hint_text_alone_never_clicked(self):
        items = [it for it in self.LEITAI_ITEMS if it[0] != "开始匹配"]
        ctx = FakeContext(ocr_items=items)
        result = self.analyze(ctx, leitai_max_wait=0.005)
        self.assertIsNone(result.box)

    def test_team_full_hint_never_matches(self):
        # 「队伍组满5人方可开始擂台乱斗匹配」是擂台面板常驻文字，
        # 旧关键词 + 包含匹配本该踩它，锚定后连候选都不算。
        ctx = FakeContext(ocr_items=[("队伍组满5人方可开始擂台乱斗匹配", FakeRect(60, 900, 420, 26), 0.97)])
        result = self.analyze(ctx, leitai_max_wait=0.005)
        self.assertIsNone(result.box)

    def test_rect_dataclass_candidates_are_parsed(self):
        ctx = FakeContext(ocr_items=[("开始匹配", FakeRect(11, 22, 33, 44), 0.9)])
        self.assertEqual(self.analyze(ctx).box, [11, 22, 33, 44])


if __name__ == "__main__":
    unittest.main()
