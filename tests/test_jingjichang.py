# -*- coding: utf-8 -*-
"""竞技场挂机的体检：结构常驻 + 锚定点击 + 两行按钮合框。

竞技场踩的坑和擂台/剑会同源，另外还多一条自己的：

* ``expected`` 写成子串匹配时，面板上的说明「开始匹配后自动进入对局」照样命中，
  框架又按 ``order_by=Horizontal`` 取最靠左的框 → 点的是文字；
* 固定比例 ROI 按一张横屏截图量的，换分辨率/竖屏整块错开；
* **按钮文字是上下两行**，OCR 把「开始」「匹配」各读成一个框（真机 918x500
  实测：``开始``=[1038,577,63,37]、``匹配``=[1042,608,59,34]）。此时整段
  关键词的正则一个都不命中 —— 按钮就在屏幕上，任务却一直蹲、从没点过。
  老版本还把这套判断和点击一起塞在识别器里自己循环，跟另外两个任务的写法不一致。

所以这里既校验 pipeline 的自循环结构，也校验识别器的锚定、两行合框与守候行为。
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

PIPELINE = "jingjichang.json"
ENTRY_NODE = "jingjichang"
KEEP_NODE = "竞技场-保持匹配"

jjc_mod = load_recognizer("jingjichang.py")

# 真机 918x500 截图里按钮的两行 OCR 框（框架把截图缩放到 720 高后量的）。
REAL_TOP_LINE = ("开始", FakeRect(1038, 577, 63, 37), 1.0)
REAL_BOTTOM_LINE = ("匹配", FakeRect(1042, 608, 59, 34), 0.998)
# 合框后应覆盖两行：起点取上框左上，右下取两框最大右下。
REAL_MERGED_BOX = [1038, 577, 63, 65]


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
        # 跟擂台/剑会同构：识别器只负责「有没有按钮」，点击由 pipeline 的
        # Click 完成（点击落点取自识别命中框）。
        keep = self.pipe[KEEP_NODE]
        self.assertEqual(keep["recognition"], "Custom")
        self.assertEqual(keep["custom_recognition"], "jingjichang")
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

    def test_no_leftover_click_count_param(self):
        # 点几次就收工的老参数一并删掉：常驻语义下它只会带来「点够次数就停」。
        blob = self.pipe[KEEP_NODE].get("custom_recognition_param") or {}
        self.assertNotIn("jjc_max_clicks", blob)


class JingjichangBehaviorTest(unittest.TestCase):
    def setUp(self):
        jjc_mod.time = type(jjc_mod.time)()
        jjc_mod.Jingjichang._LAST_HIT_TS = 0.0
        jjc_mod.Jingjichang._LAST_NOTIFY_TS = 0.0

    def analyze(self, context, **params):
        params.setdefault("jjc_roi", [10, 10, 100, 40])
        params.setdefault("jjc_interval", 0.001)
        return jjc_mod.Jingjichang().analyze(context, make_arg(**params))

    def test_hit_returns_box_for_click(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        result = self.analyze(ctx)
        self.assertEqual(result.box, [10, 10, 100, 20])
        self.assertIn("命中按钮", result.detail)

    def test_default_scan_is_whole_screen_with_anchored_patterns(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        self.analyze(ctx, jjc_roi=None)
        override = (ctx.last_override or {})[jjc_mod.Jingjichang._ROC_NAME]
        self.assertEqual(override["roi"], [0, 0, 0, 0])
        self.assertEqual(
            jjc_mod.DEFAULT_EXPECTED,
            [r"^\s*开始匹配\s*$", r"^\s*开始\s*$", r"^\s*匹配\s*$"],
        )
        self.assertEqual(override["expected"], jjc_mod.DEFAULT_EXPECTED)

    def test_round_timeout_keeps_polling_and_does_not_raise(self):
        ctx = FakeContext(ocr_items=[])
        result = self.analyze(ctx, jjc_max_wait=0.005)
        self.assertIsNone(result.box)
        self.assertIn("继续守候", result.detail)
        self.assertGreater(ctx.recognition_calls, 1)

    def test_stopping_request_exits_immediately(self):
        ctx = FakeContext(ocr_items=[])
        ctx.tasker.stopping = True
        result = self.analyze(ctx, jjc_max_wait=300)
        self.assertIsNone(result.box)
        self.assertIn("停止", result.detail)
        self.assertLessEqual(ctx.tasker.controller.count, 1)

    def test_repeated_hits_are_spaced_by_cooldown(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        self.analyze(ctx)
        before = jjc_mod.time.now
        self.analyze(ctx, jjc_click_cooldown=0.5)
        self.assertGreaterEqual(jjc_mod.time.now - before, 0.5)

    def test_cooldown_state_survives_new_instances(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        self.analyze(ctx)
        self.assertGreater(jjc_mod.Jingjichang._LAST_HIT_TS, 0.0)

    def test_notify_is_throttled(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        sent = []
        original = jjc_mod.send_message
        jjc_mod.send_message = lambda *a, **k: (sent.append(a), True)[1]
        try:
            for _ in range(3):
                self.analyze(ctx, jjc_notify=True, jjc_click_cooldown=0.0)
        finally:
            jjc_mod.send_message = original
        self.assertEqual(len(sent), 1)

    def test_screenshot_error_is_tolerated(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        ctx.tasker.controller.error = RuntimeError("adb 掉线")
        result = self.analyze(ctx, jjc_max_wait=0.005)
        self.assertIsNone(result.box)
        self.assertIn("继续守候", result.detail)

    def test_ocr_error_is_tolerated(self):
        ctx = FakeContext(reco_error=RuntimeError("OCR 崩了"))
        result = self.analyze(ctx, jjc_max_wait=0.005)
        self.assertIsNone(result.box)
        self.assertIn("继续守候", result.detail)

    def test_ratio_roi_is_used_when_explicitly_given(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        self.analyze(ctx, jjc_roi=None, jjc_roi_ratio=[0.76, 0.75, 0.18, 0.23])
        override = (ctx.last_override or {})[jjc_mod.Jingjichang._ROC_NAME]
        # 1080x607 的假帧 → 比例换算成绝对像素
        self.assertEqual(override["roi"], [820, 455, 194, 139])

    def test_disabled_short_circuits(self):
        ctx = FakeContext(ocr_items=["开始匹配"])
        result = self.analyze(ctx, jjc_enabled=False)
        self.assertIsNone(result.box)
        self.assertIn("已禁用", result.detail)
        self.assertEqual(ctx.tasker.controller.count, 0)

    # ---- 回归：真机按钮是上下两行，必须合框后点中 ----

    def test_two_line_button_is_merged_into_one_box(self):
        ctx = FakeContext(ocr_items=[REAL_TOP_LINE, REAL_BOTTOM_LINE])
        result = self.analyze(ctx)
        self.assertEqual(result.box, REAL_MERGED_BOX)
        self.assertIn("两行按钮合框", result.detail)

    def test_two_line_button_win_even_when_framework_filter_drops_everything(self):
        # 框架按 expected 过滤后只剩空表时，候选要退回 all_results 才看得见两行。
        ctx = FakeContext(ocr_items=[REAL_TOP_LINE, REAL_BOTTOM_LINE])
        original = FakeContext.run_recognition

        def drop_filtered(self, name, image, pipeline_override=None):
            reco = original(self, name, image, pipeline_override)
            reco.filtered_results = []
            return reco

        FakeContext.run_recognition = drop_filtered
        try:
            result = self.analyze(ctx)
        finally:
            FakeContext.run_recognition = original
        self.assertEqual(result.box, REAL_MERGED_BOX)

    ARENA_DECOY = ("开始匹配后自动进入对局", FakeRect(300, 200, 240, 26), 0.99)

    def test_decoy_line_never_becomes_the_button(self):
        ctx = FakeContext(ocr_items=[self.ARENA_DECOY])
        result = self.analyze(ctx, jjc_max_wait=0.005)
        self.assertIsNone(result.box)

    def test_decoy_line_does_not_shadow_the_two_line_button(self):
        ctx = FakeContext(ocr_items=[self.ARENA_DECOY, REAL_TOP_LINE, REAL_BOTTOM_LINE])
        result = self.analyze(ctx)
        self.assertEqual(result.box, REAL_MERGED_BOX)

    def test_single_line_button_still_works(self):
        # 其它分辨率/机型上按钮仍是单行的，合框逻辑不能把它弄丢。
        ctx = FakeContext(ocr_items=[("开始匹配", FakeRect(760, 780, 120, 45), 0.95)])
        self.assertEqual(self.analyze(ctx).box, [760, 780, 120, 45])

    def test_split_halves_far_apart_are_not_a_button(self):
        items = [
            ("开始", FakeRect(100, 100, 60, 34), 0.99),
            ("匹配", FakeRect(900, 900, 60, 34), 0.99),
        ]
        ctx = FakeContext(ocr_items=items)
        result = self.analyze(ctx, jjc_max_wait=0.005)
        self.assertIsNone(result.box)

    def test_half_keyword_alone_is_not_a_button(self):
        ctx = FakeContext(ocr_items=[REAL_TOP_LINE])
        result = self.analyze(ctx, jjc_max_wait=0.005)
        self.assertIsNone(result.box)


if __name__ == "__main__":
    unittest.main()
