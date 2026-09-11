# -*- coding: utf-8 -*-
"""截图链路守卫测试：画面冻结 / 截图连续失败 / 断连判定、告警控频、恢复通知。

回归背景：模拟器掉线后 MaaFramework 会丢弃新截图请求，画面永远停在最后一帧。
旧实现毫无反应 —— 单轮盲跑 49 分钟才到扫描上限，随后管线去跑"必须新截图"的收尾
节点，超时 → 整条任务被判 ``Tasker.Task.Failed``，表现就是"总是跑一段时间就失败"。
"""

import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_UTILS = REPO_ROOT / "agent" / "utils"
LINK_GUARD_PATH = AGENT_UTILS / "link_guard.py"


def load_link_guard():
    if str(AGENT_UTILS) not in sys.path:
        sys.path.insert(0, str(AGENT_UTILS))
    spec = importlib.util.spec_from_file_location("link_guard_under_test", LINK_GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lg = load_link_guard()


class LinkGuardFreezeTest(unittest.TestCase):
    """画面冻结判定。"""

    def test_not_frozen_before_threshold(self):
        guard = lg.LinkGuard(freeze_seconds=60.0, screencap_fail_limit=5)
        guard.on_frame(False, 1000.0)
        self.assertEqual(guard.state(1059.9), lg.STATE_OK)
        self.assertAlmostEqual(guard.frozen_seconds(1059.9), 59.9, places=4)

    def test_frozen_at_threshold(self):
        guard = lg.LinkGuard(freeze_seconds=60.0, screencap_fail_limit=5)
        guard.on_frame(False, 1000.0)
        self.assertEqual(guard.state(1060.0), lg.STATE_FROZEN)

    def test_changed_frame_resets_freeze_clock(self):
        guard = lg.LinkGuard(freeze_seconds=60.0, screencap_fail_limit=5)
        guard.on_frame(False, 1000.0)
        guard.on_frame(True, 1030.0)
        self.assertEqual(guard.frozen_seconds(1030.0), 0.0)
        guard.on_frame(False, 1040.0)
        self.assertEqual(guard.state(1099.0), lg.STATE_OK)
        self.assertEqual(guard.state(1100.0), lg.STATE_FROZEN)

    def test_reset_round_clears_freeze_clock(self):
        guard = lg.LinkGuard(freeze_seconds=60.0, screencap_fail_limit=5)
        guard.on_frame(False, 1000.0)
        guard.reset_round()
        self.assertEqual(guard.state(2000.0), lg.STATE_OK)


class LinkGuardScreencapTest(unittest.TestCase):
    """截图连续失败判定。"""

    def test_fails_below_limit_are_ok(self):
        guard = lg.LinkGuard(freeze_seconds=60.0, screencap_fail_limit=3)
        for i in range(2):
            guard.on_screencap_fail(1000.0 + i)
        self.assertEqual(guard.screencap_fails, 2)
        self.assertEqual(guard.state(1002.0), lg.STATE_OK)

    def test_fails_at_limit(self):
        guard = lg.LinkGuard(freeze_seconds=60.0, screencap_fail_limit=3)
        for i in range(3):
            guard.on_screencap_fail(1000.0 + i)
        self.assertEqual(guard.state(1003.0), lg.STATE_SCREENCAP)

    def test_screencap_ok_clears_fail_streak(self):
        guard = lg.LinkGuard(freeze_seconds=60.0, screencap_fail_limit=3)
        for i in range(3):
            guard.on_screencap_fail(1000.0 + i)
        guard.on_screencap_ok(1004.0)
        self.assertEqual(guard.screencap_fails, 0)
        self.assertEqual(guard.state(1005.0), lg.STATE_OK)

    def test_screencap_state_wins_over_frozen(self):
        guard = lg.LinkGuard(freeze_seconds=10.0, screencap_fail_limit=2)
        guard.on_frame(False, 1000.0)
        for i in range(2):
            guard.on_screencap_fail(1000.0 + i)
        self.assertEqual(guard.state(1100.0), lg.STATE_SCREENCAP)


class LinkGuardAlertTest(unittest.TestCase):
    """告警控频：同一种故障在冷却期内只发一次，换故障类型立即发。"""

    def test_no_alert_when_healthy(self):
        guard = lg.LinkGuard(alert_cooldown=1800.0)
        self.assertFalse(guard.should_alert(lg.STATE_OK, 1000.0))

    def test_first_alert_immediate(self):
        guard = lg.LinkGuard(alert_cooldown=1800.0)
        self.assertTrue(guard.should_alert(lg.STATE_FROZEN, 1000.0))

    def test_same_state_within_cooldown_does_not_realert(self):
        guard = lg.LinkGuard(alert_cooldown=1800.0)
        guard.mark_alerted(lg.STATE_FROZEN, 1000.0, 200.0)
        self.assertFalse(guard.should_alert(lg.STATE_FROZEN, 2799.0))
        self.assertTrue(guard.should_alert(lg.STATE_FROZEN, 2800.0))

    def test_different_state_alerts_after_min_gap(self):
        """换了故障类型可以在冷却期之前发，但仍受最小间隔约束（防来回刷）。"""
        guard = lg.LinkGuard(alert_cooldown=1800.0)
        guard.mark_alerted(lg.STATE_FROZEN, 1000.0, 200.0)
        self.assertFalse(guard.should_alert(lg.STATE_SCREENCAP, 1000.0 + lg.MIN_ALERT_GAP_SECONDS / 2))
        self.assertTrue(guard.should_alert(lg.STATE_SCREENCAP, 1000.0 + lg.MIN_ALERT_GAP_SECONDS))

    def test_alert_bookkeeping_survives_reset_round(self):
        """每轮都会 reset_round，冷却时间戳必须跨轮保留，否则每轮都重发告警。"""
        guard = lg.LinkGuard(alert_cooldown=1800.0)
        guard.mark_alerted(lg.STATE_FROZEN, 1000.0, 300.0)
        guard.reset_round()
        self.assertEqual(guard.alert_count, 1)
        self.assertFalse(guard.should_alert(lg.STATE_FROZEN, 1000.0 + 60.0))

    def test_recovery_notice_needs_prior_alert(self):
        guard = lg.LinkGuard(alert_cooldown=1800.0)
        self.assertFalse(guard.needs_recovery_notice(lg.STATE_OK))
        guard.mark_alerted(lg.STATE_FROZEN, 1000.0, 200.0)
        for i in range(lg.RECOVER_CONFIRM_FRAMES):
            guard.on_frame(True, 1000.0 + i)
        self.assertTrue(guard.needs_recovery_notice(lg.STATE_OK))
        self.assertFalse(guard.needs_recovery_notice(lg.STATE_FROZEN))
        guard.mark_recovered()
        self.assertFalse(guard.needs_recovery_notice(lg.STATE_OK))

    def test_recovery_notice_requires_sustained_change(self):
        """掉线后下一轮第一帧必然"看起来变了"，只有连续多帧在刷新才算真恢复。"""
        guard = lg.LinkGuard(alert_cooldown=1800.0)
        guard.mark_alerted(lg.STATE_FROZEN, 1000.0, 200.0)
        for i in range(lg.RECOVER_CONFIRM_FRAMES - 1):
            guard.on_frame(True, 1000.0 + i)
        self.assertFalse(guard.needs_recovery_notice(lg.STATE_OK))
        guard.on_frame(True, 1100.0)
        self.assertTrue(guard.needs_recovery_notice(lg.STATE_OK))

    def test_frozen_frame_clears_recover_streak(self):
        guard = lg.LinkGuard(alert_cooldown=1800.0)
        guard.mark_alerted(lg.STATE_FROZEN, 1000.0, 10.0)
        for i in range(lg.RECOVER_CONFIRM_FRAMES + 2):
            guard.on_frame(True, 1000.0 + i)
        guard.on_frame(False, 1010.0)
        self.assertFalse(guard.needs_recovery_notice(lg.STATE_OK))

    def test_reset_round_clears_recover_streak(self):
        guard = lg.LinkGuard(alert_cooldown=1800.0)
        guard.mark_alerted(lg.STATE_FROZEN, 1000.0, 10.0)
        for i in range(lg.RECOVER_CONFIRM_FRAMES + 2):
            guard.on_frame(True, 1000.0 + i)
        guard.reset_round()
        self.assertFalse(guard.needs_recovery_notice(lg.STATE_OK))

    def test_min_alert_gap_applies_between_different_states(self):
        guard = lg.LinkGuard(alert_cooldown=1800.0)
        guard.mark_alerted(lg.STATE_FROZEN, 1000.0)
        self.assertFalse(
            guard.should_alert(lg.STATE_SCREENCAP, 1000.0 + lg.MIN_ALERT_GAP_SECONDS - 1.0)
        )
        self.assertTrue(
            guard.should_alert(lg.STATE_SCREENCAP, 1000.0 + lg.MIN_ALERT_GAP_SECONDS)
        )

    def test_frozen_total_keeps_longest_freeze(self):
        guard = lg.LinkGuard(alert_cooldown=10.0)
        guard.mark_alerted(lg.STATE_FROZEN, 1000.0, 200.0)
        guard.mark_alerted(lg.STATE_FROZEN, 5000.0, 1900.0)
        self.assertEqual(guard.frozen_total, 1900.0)
        guard.mark_recovered()
        self.assertEqual(guard.frozen_total, 0.0)


class LinkGuardClassifyTest(unittest.TestCase):
    """connected 属性与守卫状态的合成判定。"""

    def test_disconnect_with_fail_evidence(self):
        self.assertEqual(
            lg.classify(guard_state=lg.STATE_OK, connected=False,
                        screencap_fails=lg.DISCONNECT_CONFIRM_FAILS, frozen_seconds=0.0),
            lg.STATE_DISCONNECTED,
        )

    def test_disconnect_with_freeze_evidence(self):
        self.assertEqual(
            lg.classify(guard_state=lg.STATE_OK, connected=False, screencap_fails=0,
                        frozen_seconds=lg.DISCONNECT_CONFIRM_FROZEN_SECONDS),
            lg.STATE_DISCONNECTED,
        )

    def test_disconnect_without_evidence_is_not_reported(self):
        """个别平台 connected 值不准：没有截图失败/画面静止旁证就不误报。"""
        self.assertEqual(
            lg.classify(guard_state=lg.STATE_OK, connected=False,
                        screencap_fails=0, frozen_seconds=1.0),
            lg.STATE_OK,
        )

    def test_unknown_connection_falls_back_to_guard_state(self):
        self.assertEqual(
            lg.classify(guard_state=lg.STATE_FROZEN, connected=None,
                        screencap_fails=0, frozen_seconds=999.0),
            lg.STATE_FROZEN,
        )

    def test_connected_true_keeps_guard_state(self):
        self.assertEqual(
            lg.classify(guard_state=lg.STATE_SCREENCAP, connected=True,
                        screencap_fails=99, frozen_seconds=999.0),
            lg.STATE_SCREENCAP,
        )


class LinkGuardTextTest(unittest.TestCase):
    """告警 / 恢复文案。"""

    def test_freeze_alert_mentions_duration(self):
        text = lg.alert_text(lg.STATE_FROZEN, frozen_seconds=185.0)
        self.assertIn("冻结", text)
        self.assertIn("3 分 5 秒", text)

    def test_screencap_alert_mentions_count(self):
        text = lg.alert_text(lg.STATE_SCREENCAP, screencap_fails=30)
        self.assertIn("30 次截图失败", text)

    def test_disconnect_alert_text(self):
        text = lg.alert_text(lg.STATE_DISCONNECTED)
        self.assertIn("断连", text)

    def test_alert_includes_recent_error_truncated(self):
        text = lg.alert_text(lg.STATE_SCREENCAP, screencap_fails=3, last_error="x" * 300)
        self.assertIn("最近错误", text)
        self.assertLessEqual(len(text.split("最近错误：")[1].splitlines()[0]), 120)

    def test_recovery_text_variants(self):
        self.assertIn("连接已恢复", lg.recovery_text(alerted_state=lg.STATE_DISCONNECTED))
        self.assertIn("画面已恢复刷新", lg.recovery_text(frozen_seconds=185.0,
                                                         alerted_state=lg.STATE_FROZEN))

    def test_state_text_mapping(self):
        self.assertEqual(lg.state_text(lg.STATE_FROZEN), "画面冻结")
        self.assertEqual(lg.state_text(lg.STATE_SCREENCAP), "截图连续失败")
        self.assertEqual(lg.state_text(lg.STATE_DISCONNECTED), "与模拟器断连")
        self.assertEqual(lg.state_text(lg.STATE_OK), "")

    def test_duration_format(self):
        self.assertEqual(lg._fmt_duration(45.0), "45 秒")
        self.assertEqual(lg._fmt_duration(185.0), "3 分 5 秒")
        self.assertEqual(lg._fmt_duration(-10.0), "0 秒")


class LinkGuardConfigTest(unittest.TestCase):
    """默认值与 configure 覆盖。"""

    def test_defaults(self):
        self.assertEqual(lg.DEFAULT_FREEZE_SECONDS, 180.0)
        self.assertEqual(lg.DEFAULT_SCREENCAP_FAIL_LIMIT, 30)
        self.assertEqual(lg.DEFAULT_ALERT_COOLDOWN_SECONDS, 1800.0)
        self.assertGreaterEqual(lg.DEFAULT_FREEZE_SECONDS,
                                lg.DISCONNECT_CONFIRM_FROZEN_SECONDS)

    def test_configure_overrides_thresholds(self):
        guard = lg.LinkGuard()
        guard.configure(freeze_seconds=30, screencap_fail_limit=2, alert_cooldown=60)
        self.assertEqual(guard.freeze_seconds, 30.0)
        self.assertEqual(guard.screencap_fail_limit, 2)
        self.assertEqual(guard.alert_cooldown, 60.0)
        guard.on_frame(False, 1000.0)
        self.assertEqual(guard.state(1030.0), lg.STATE_FROZEN)

    def test_configure_ignores_none(self):
        guard = lg.LinkGuard(freeze_seconds=120.0, screencap_fail_limit=7, alert_cooldown=90.0)
        guard.configure()
        self.assertEqual((guard.freeze_seconds, guard.screencap_fail_limit, guard.alert_cooldown),
                         (120.0, 7, 90.0))

    def test_configure_clamps_bad_values(self):
        guard = lg.LinkGuard()
        guard.configure(freeze_seconds=0, screencap_fail_limit=0, alert_cooldown=-5)
        self.assertEqual(guard.freeze_seconds, 1.0)
        self.assertEqual(guard.screencap_fail_limit, 1)
        self.assertEqual(guard.alert_cooldown, 0.0)


class YaowangGuardWiringTest(unittest.TestCase):
    """agent 侧接线：源码里必须真的用上守卫（防止以后被改回去）。"""

    def setUp(self):
        self.source = (REPO_ROOT / "agent" / "custom" / "recognition" / "yaowang.py").read_text(
            encoding="utf-8"
        )

    def test_imports_and_uses_guard(self):
        self.assertIn("from utils import link_guard", self.source)
        self.assertIn("_LINK_GUARD = link_guard.LinkGuard()", self.source)
        self.assertIn("_LINK_GUARD.reset_round()", self.source)
        self.assertIn("_LINK_GUARD.on_frame(changed, now)", self.source)
        self.assertIn("_LINK_GUARD.on_screencap_fail(now)", self.source)
        self.assertIn("_LINK_GUARD.on_screencap_ok(now)", self.source)

    def test_guard_exit_paths_are_clean(self):
        """链路失效与停止请求都必须干净收尾（返回结果，而不是抛异常）。"""
        self.assertIn("截图链路失效，本轮告警收尾", self.source)
        self.assertIn("画面冻结，本轮告警收尾", self.source)
        self.assertIn("任务已停止", self.source)
        self.assertIn("_tasker_stopping", self.source)

    def test_alert_and_recovery_use_sender(self):
        self.assertIn("link_guard.alert_text(", self.source)
        self.assertIn("link_guard.recovery_text(", self.source)


class YaowangPipelineTest(unittest.TestCase):
    """管线必须保证"掉线也不会把整条任务拖成失败"。"""

    def setUp(self):
        import json

        path = REPO_ROOT / "assets" / "resource" / "base" / "pipeline" / "yaowang_pipe.json"
        self.pipeline = json.loads(path.read_text(encoding="utf-8"))

    def test_entry_node_has_no_screenshot_dependent_tail(self):
        """收尾节点 ``panduan_zhujiemian`` 必须已移除：截图挂掉时该节点会超时并判整任务失败。"""
        next_list = self.pipeline["yaowang_pipe"]["next"]
        self.assertEqual(next_list, ["蹲妖王-监测循环"])
        self.assertNotIn("panduan_zhujiemian", json_dumps(self.pipeline))

    def test_timeout_is_infinite(self):
        """timeout=-1（无限等待）：默认 20s 会因单轮扫描 80s 而每轮都走 on_error 链，
        框架的 error-loop 保护积累到一定程度就把任务判失败。"""
        self.assertEqual(self.pipeline["yaowang_pipe"]["timeout"], -1)
        self.assertEqual(self.pipeline["蹲妖王-监测循环"]["timeout"], -1)

    def test_loop_keeps_itself_alive(self):
        node = self.pipeline["蹲妖王-监测循环"]
        self.assertEqual(node["recognition"], "Custom")
        self.assertEqual(node["custom_recognition"], "yaowang")
        self.assertEqual(node["next"], ["[JumpBack]蹲妖王-监测循环"])
        self.assertEqual(node["on_error"], ["[JumpBack]蹲妖王-监测循环"])


def json_dumps(data):
    import json

    return json.dumps(data, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
