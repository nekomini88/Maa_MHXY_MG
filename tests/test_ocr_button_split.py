# -*- coding: utf-8 -*-
"""``utils.ocr_button`` 的框选规则：整段锚定 + 两行按钮合框。

三个同类任务（擂台 / 竞技场 / 剑会）共用这一个挑框函数，所以几何判据单独测，
不走识别器。真机依据：竞技场 918x500 截图里按钮文字排成上下两行，OCR 各给一个
框 —— ``开始``=[1038,577,63,37]、``匹配``=[1042,608,59,34]。
"""

import unittest

from maa_stubs import FakeOcrItem, FakeReco, install_stubs, mk_ocr_items

install_stubs()

from utils.ocr_button import expected_patterns, keyword_halves, pick_button_box  # noqa: E402

TOP_LINE = ("开始", (1038, 577, 63, 37), 1.0)
BOTTOM_LINE = ("匹配", (1042, 608, 59, 34), 0.998)
MERGED = [1038, 577, 63, 65]


def reco(*items):
    return FakeReco(mk_ocr_items(list(items)))


class KeywordSplitTest(unittest.TestCase):
    def test_halves_of_four_char_keyword(self):
        self.assertEqual(keyword_halves("开始匹配"), ("开始", "匹配"))

    def test_short_keyword_has_no_halves(self):
        self.assertEqual(keyword_halves("开始"), ())

    def test_expected_patterns_cover_whole_and_halves(self):
        self.assertEqual(
            expected_patterns(),
            [r"^\s*开始匹配\s*$", r"^\s*开始\s*$", r"^\s*匹配\s*$"],
        )


class PickButtonBoxTest(unittest.TestCase):
    def test_single_box_exact_text(self):
        box, why = pick_button_box(reco(("开始匹配", (760, 780, 120, 45), 0.95)))
        self.assertEqual(box, [760, 780, 120, 45])
        self.assertIn("开始匹配", why)

    def test_two_line_button_is_merged(self):
        box, why = pick_button_box(reco(TOP_LINE, BOTTOM_LINE))
        self.assertEqual(box, MERGED)
        self.assertIn("两行按钮合框", why)

    def test_two_line_button_survives_other_screen_text(self):
        box, _ = pick_button_box(
            reco(
                ("开始匹配后自动进入对局", (300, 200, 240, 26), 0.99),
                ("今日已完成 0/10", (900, 700, 150, 24), 0.97),
                TOP_LINE,
                BOTTOM_LINE,
            )
        )
        self.assertEqual(box, MERGED)

    def test_hint_text_alone_is_not_a_button(self):
        box, _ = pick_button_box(reco(("开始匹配后自动进入对局", (300, 200, 240, 26), 0.99)))
        self.assertIsNone(box)

    def test_single_half_is_not_a_button(self):
        self.assertIsNone(pick_button_box(reco(TOP_LINE))[0])
        self.assertIsNone(pick_button_box(reco(BOTTOM_LINE))[0])

    def test_halves_stacked_horizontally_are_not_merged(self):
        # 同一行并排的「开始」「匹配」：竖直间距/水平重叠任一不成立都不合框。
        items = [("开始", (100, 100, 60, 34), 0.99), ("匹配", (170, 100, 60, 34), 0.99)]
        self.assertIsNone(pick_button_box(reco(*items))[0])

    def test_halves_far_apart_vertically_are_not_merged(self):
        items = [("开始", (100, 100, 60, 34), 0.99), ("匹配", (100, 900, 60, 34), 0.99)]
        self.assertIsNone(pick_button_box(reco(*items))[0])

    def test_reversed_order_is_not_merged(self):
        # 「匹配」在上、「开始」在下：不是按钮的排法，不合框。
        items = [("开始", (1038, 608, 63, 37), 1.0), ("匹配", (1042, 577, 59, 34), 1.0)]
        self.assertIsNone(pick_button_box(reco(*items))[0])

    def test_much_narrower_second_line_is_not_merged(self):
        items = [("开始", (100, 100, 200, 34), 0.99), ("匹配", (100, 140, 40, 34), 0.99)]
        self.assertIsNone(pick_button_box(reco(*items))[0])

    def test_blacklisted_text_is_skipped(self):
        box, _ = pick_button_box(reco(("请及时开始匹配", (10, 10, 200, 30), 0.99)))
        self.assertIsNone(box)

    def test_best_pair_is_chosen_by_score_then_area(self):
        items = [
            ("开始", (100, 100, 60, 34), 0.70),
            ("匹配", (102, 132, 60, 34), 0.70),
            TOP_LINE,
            BOTTOM_LINE,
        ]
        box, _ = pick_button_box(reco(*items))
        self.assertEqual(box, MERGED)

    def test_dataclass_box_candidates_are_parsed(self):
        from maa_stubs import FakeRect

        box, _ = pick_button_box(reco(("开始", FakeRect(1038, 577, 63, 37), 1.0),
                                      ("匹配", FakeRect(1042, 608, 59, 34), 1.0)))
        self.assertEqual(box, MERGED)

    def test_empty_result_returns_none(self):
        self.assertIsNone(pick_button_box(FakeReco([]))[0])
        self.assertIsNone(pick_button_box(None)[0])


if __name__ == "__main__":
    unittest.main()
