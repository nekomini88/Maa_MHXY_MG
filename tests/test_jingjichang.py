# -*- coding: utf-8 -*-
"""竞技场挂机的体检：锚定点击 + 打满次数（可选常驻）。

竞技场和擂台/剑会共用同一套识别的坑：`expected` 是子串正则、框架取最靠左的
命中框，面板上的「开始匹配后自动进入对局」这类说明文字会被当成按钮点掉；
固定比例 ROI 也扛不住换分辨率。所以这里校验锚定逻辑，另外兜住两条竞技场
特有的语义：

* 点击次数挂在**类**上（analyze 每次都可能是新实例，写实例上会让计数归零，
  pipeline 的 on_error 重试会让它越点越多）；
* `jjc_max_clicks <= 0` 表示不限次数/常驻，此时永远不返回成功，直到手动停止。

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
WORK_NODE = "竞技场-开始挂机"

HIT = [["开始匹配"]]

jjc_mod = load_recognizer("jingjichang.py")


class _SequenceContext(FakeContext):
    """按调用顺序送出预设帧内容，最后一项一直重复（模拟按钮被点掉后消失）。"""

    def __init__(self, frames, **kwargs):
        super().__init__(**kwargs)
        self._frames = list(frames) or [[]]
        self._seen = 0

    def run_recognition(self, name, image, pipeline_override=None):
        self._seen += 1
        index = min(self._seen, len(self._frames)) - 1
        self.ocr_items = self._frames[index]
        return super().run_recognition(name, image, pipeline_override)


class PipelineStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipe = load_pipeline(PIPELINE)
        cls.all_nodes = all_pipeline_nodes()

    def test_only_entry_and_work_node_exist(self):
        self.assertEqual(set(self.pipe), {ENTRY_NODE, WORK_NODE})

    def test_entry_never_times_out(self):
        entry = self.pipe[ENTRY_NODE]
        self.assertEqual(entry["timeout"], -1)
        self.assertEqual(entry["next"], [WORK_NODE])

    def test_work_node_never_times_out_on_long_custom_recognition(self):
        # 挂机节点要连续蹲满 10 场，节点超时不能说杀就杀。
        self.assertEqual(self.pipe[WORK_NODE]["timeout"], -1)

    def test_work_node_recognizes_with_custom_reco(self):
        work = self.pipe[WORK_NODE]
        self.assertEqual(work["recognition"], "Custom")
        self.assertEqual(work["custom_recognition"], "jingjichang")
        self.assertEqual(work["custom_recognition_param"]["jjc_max_clicks"], 10)
        # 点击发生在识别器内部（点击次数即场次），节点本身不再点一次。
        self.assertNotIn("action", work)

    def test_wait_timeout_retries_same_node_keeping_count(self):
        self.assertEqual(self.pipe[WORK_NODE]["on_error"], [f"[JumpBack]{WORK_NODE}"])

    def test_no_dangling_node_reference(self):
        for name, key, target in node_refs(self.pipe):
            with self.subTest(node=name, key=key, ref=target):
                self.assertIn(target, self.all_nodes)


class JingjichangBehaviorTest(unittest.TestCase):
    def setUp(self):
        jjc_mod.time = type(jjc_mod.time)()
        jjc_mod.Jingjichang._CLICK_COUNT = 0
        jjc_mod.Jingjichang._LAST_NOTIFY_TS = 0.0

    def analyze(self, context, **params):
        params.setdefault("jjc_roi", [10, 10, 100, 40])
        params.setdefault("jjc_interval", 0.001)
        params.setdefault("jjc_click_delay", 0.0)
        return jjc_mod.Jingjichang().analyze(context, make_arg(**params))

    def test_clicks_button_max_times_then_reports_done(self):
        ctx = _SequenceContext(HIT)
        result = self.analyze(ctx, jjc_max_clicks=3)
        self.assertEqual(ctx.tasker.controller.clicks, [(60, 20)] * 3)
        self.assertEqual(result.box, [10, 10, 100, 20])
        self.assertIn("打满3次", result.detail)

    def test_click_lands_on_button_center(self):
        ctx = _SequenceContext(HIT)
        self.analyze(ctx, jjc_max_clicks=1)
        self.assertEqual(ctx.tasker.controller.clicks, [(60, 20)])

    def test_counter_resets_after_completion(self):
        ctx = _SequenceContext(HIT)
        self.analyze(ctx, jjc_max_clicks=2)
        self.assertEqual(jjc_mod.Jingjichang._CLICK_COUNT, 0)

    def test_counter_persists_across_instances(self):
        # pipeline 的 on_error 会重新进入 analyze（新实例），计数不能归零。
        first = _SequenceContext([["开始匹配"], []])
        result = self.analyze(first, jjc_max_clicks=3, jjc_max_wait=0.02)
        self.assertIsNone(result.box)
        self.assertEqual(jjc_mod.Jingjichang._CLICK_COUNT, 1)
        second = _SequenceContext(HIT)
        result = self.analyze(second, jjc_max_clicks=3)
        self.assertIn("打满3次", result.detail)
        self.assertEqual(
            len(first.tasker.controller.clicks) + len(second.tasker.controller.clicks),
            3,
        )

    def test_wait_timeout_keeps_count_and_reports_failure(self):
        ctx = _SequenceContext([[]])
        result = self.analyze(ctx, jjc_max_clicks=3, jjc_max_wait=0.02)
        self.assertIsNone(result.box)
        self.assertIn("已点0次", result.detail)
        self.assertEqual(jjc_mod.Jingjichang._CLICK_COUNT, 0)
        self.assertEqual(ctx.tasker.controller.clicks, [])

    def test_unlimited_clicks_never_completes(self):
        # jjc_max_clicks <= 0：一直挂，不会因为到了次数就收工。
        ctx = _SequenceContext(HIT * 3 + [[]])
        result = self.analyze(ctx, jjc_max_clicks=0, jjc_max_wait=0.02)
        self.assertEqual(len(ctx.tasker.controller.clicks), 3)
        self.assertIsNone(result.box)
        self.assertNotIn("打满", result.detail)
        self.assertEqual(jjc_mod.Jingjichang._CLICK_COUNT, 3)

    def test_stopping_request_exits_without_clicking(self):
        ctx = _SequenceContext(HIT)
        ctx.tasker.stopping = True
        result = self.analyze(ctx, jjc_max_clicks=3, jjc_max_wait=300)
        self.assertIsNone(result.box)
        self.assertIn("停止", result.detail)
        self.assertEqual(ctx.tasker.controller.clicks, [])

    def test_default_scan_is_whole_screen_with_anchored_pattern(self):
        ctx = _SequenceContext(HIT)
        self.analyze(ctx, jjc_roi=None, jjc_max_clicks=1)
        override = (ctx.last_override or {})[jjc_mod.Jingjichang._ROC_NAME]
        self.assertEqual(override["roi"], [0, 0, 0, 0])
        self.assertEqual(jjc_mod.DEFAULT_EXPECTED, [r"^\s*开始匹配\s*$"])
        self.assertEqual(override["expected"], [r"^\s*开始匹配\s*$"])

    def test_ratio_roi_is_used_when_explicitly_given(self):
        ctx = _SequenceContext(HIT)
        self.analyze(
            ctx,
            jjc_roi=None,
            jjc_roi_ratio=[0.76, 0.75, 0.18, 0.23],
            jjc_max_clicks=1,
        )
        override = (ctx.last_override or {})[jjc_mod.Jingjichang._ROC_NAME]
        self.assertEqual(override["roi"], [820, 455, 194, 139])

    def test_screenshot_error_is_tolerated(self):
        ctx = _SequenceContext(HIT)
        ctx.tasker.controller.error = RuntimeError("adb 掉线")
        result = self.analyze(ctx, jjc_max_clicks=3, jjc_max_wait=0.02)
        self.assertIsNone(result.box)
        self.assertEqual(ctx.tasker.controller.clicks, [])

    def test_disabled_short_circuits(self):
        ctx = _SequenceContext(HIT)
        result = self.analyze(ctx, jjc_enabled=False, jjc_max_clicks=3)
        self.assertEqual(ctx.tasker.controller.clicks, [])
        self.assertIn("已禁用", result.detail)

    # ---- 回归：面板上的说明文字不是按钮 ----

    JJC_ITEMS = [
        ("开始匹配后自动进入对局", FakeRect(820, 470, 300, 24), 0.98),
        ("开始匹配", FakeRect(860, 500, 120, 45), 0.95),
    ]

    def test_clicks_the_button_not_the_note(self):
        ctx = _SequenceContext([list(self.JJC_ITEMS)])
        self.analyze(ctx, jjc_max_clicks=1)
        # 860+120/2=920，500+45/2=522：点在按钮中心，不是说明文字。
        self.assertEqual(ctx.tasker.controller.clicks, [(920, 522)])

    def test_note_alone_never_clicked(self):
        ctx = _SequenceContext([[self.JJC_ITEMS[0]]])
        result = self.analyze(ctx, jjc_max_clicks=1, jjc_max_wait=0.02)
        self.assertEqual(ctx.tasker.controller.clicks, [])
        self.assertIsNone(result.box)

    def test_rect_dataclass_candidates_are_parsed(self):
        ctx = _SequenceContext([[("开始匹配", FakeRect(11, 22, 33, 44), 0.9)]])
        result = self.analyze(ctx, jjc_max_clicks=1)
        self.assertEqual(ctx.tasker.controller.clicks, [(11 + 16, 22 + 22)])
        self.assertEqual(result.box, [11, 22, 33, 44])


if __name__ == "__main__":
    unittest.main()
