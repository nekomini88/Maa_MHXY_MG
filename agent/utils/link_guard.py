# -*- coding: utf-8 -*-
"""妖王监测的截图链路守卫：画面冻结 / 连续截图失败 → 判定链路失效 + 控频告警。

回归背景（yaowang.log / maafw.log 实证）：
  模拟器或 ADB 掉线后，MaaFramework 的新截图请求会被直接丢弃
  (``ControllerAgent::check_stop stopping, ignore new post``)，妖王监测拿到的
  永远是同一帧（帧差恒为 0）。旧实现只在"连续无变化"时做一次兜底 OCR，既不告警
  也不提前收尾 → 单轮盲跑 49 分钟，跑完还去执行必须新截图的收尾节点，最终把整条
  任务拖成 ``Tasker.Task.Failed``，表现就是"总是跑一段时间就失败、不能一直监控"。

本模块只放纯逻辑（不依赖 maa / loguru / numpy），便于单测：
  * 画面冻结：连续 ``freeze_seconds`` 秒帧差为 0
  * 截图失效：连续 ``screencap_fail_limit`` 次截图抛异常或返回 None
  * 告警控频：同一种故障在 ``alert_cooldown_seconds`` 内只告警一次；
    恢复后补一条"已恢复"，避免用户误以为还在监控
"""

# 画面连续静止多少秒判定链路失效（0.25s/帧 → 180s ≈ 720 帧）
# 实测正常挂机时最长自然静止约 98 帧（≈25s），取 180s 留 7 倍余量，避免"画面本来就静止"误报。
DEFAULT_FREEZE_SECONDS = 180.0
# 连续截图失败多少次判定链路失效
DEFAULT_SCREENCAP_FAIL_LIMIT = 30
# 同一种故障的最小告警间隔秒（30 分钟）
DEFAULT_ALERT_COOLDOWN_SECONDS = 1800.0

# 控制器明确断连（MaaControllerConnected=false）时的旁证门槛：
# 只要本轮已连续截图失败这么多次、或画面已静止这么久，就立刻判定链路失效
# （避免个别平台 connected 属性不准导致误报）。
DISCONNECT_CONFIRM_FAILS = 3
DISCONNECT_CONFIRM_FROZEN_SECONDS = 30.0

# 判定"链路已恢复"需要连续这么多帧都真的有变化。
# 为什么需要：掉线那一刻画面是旧的，下一轮拿到的第一帧必然"看起来变了"（帧差 inf），
# 若立刻发「已恢复」，掉线期间每轮都会先报恢复再报异常 → 刷屏且误导。
RECOVER_CONFIRM_FRAMES = 5
# 任意两条告警之间的最小间隔（即使是不同类型故障，也避免来回刷）
MIN_ALERT_GAP_SECONDS = 60.0

STATE_OK = ""
STATE_FROZEN = "frozen"
STATE_SCREENCAP = "screencap"
STATE_DISCONNECTED = "disconnected"

ALERT_TITLE = "妖王监控异常"
RECOVERY_TITLE = "妖王监控已恢复"

_STATE_TEXT = {
    STATE_FROZEN: "画面冻结",
    STATE_SCREENCAP: "截图连续失败",
    STATE_DISCONNECTED: "与模拟器断连",
}



def state_text(state: str) -> str:
    """故障状态的中文说法（未知状态返回原文）。"""
    return _STATE_TEXT.get(state, state or "")


def _fmt_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.0f} 秒"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes} 分 {rest} 秒"


def alert_text(state: str, *, frozen_seconds: float = 0.0, screencap_fails: int = 0,
               last_error: str = "") -> str:
    """链路失效告警正文（发给 Telegram）。"""
    if state == STATE_SCREENCAP:
        head = f"连续 {screencap_fails} 次截图失败，与模拟器的连接已断（ADB 掉线 / 模拟器未响应）。"
    elif state == STATE_DISCONNECTED:
        head = "控制器报告已与模拟器断连（ADB 掉线 / 模拟器卡死 / 正在重置控制器）。"
    else:
        head = (f"游戏画面已冻结 {_fmt_duration(frozen_seconds)}（帧差恒为 0），"
                f"截图链路已失效（模拟器卡死 / ADB 掉线 / MFA 正在重置控制器）。")
    lines = [head, "已尝试自动重连，画面恢复后会继续监测并回一条『已恢复』。"]
    if last_error:
        lines.append(f"最近错误：{last_error[:120]}")
    lines.append("长时间不恢复时请检查雷电模拟器（必要时重启模拟器与 MFA）。")
    return "\n".join(lines)


def recovery_text(*, frozen_seconds: float = 0.0, alerted_state: str = "") -> str:
    """恢复通知正文。"""
    if alerted_state in (STATE_SCREENCAP, STATE_DISCONNECTED):
        return "与模拟器的连接已恢复，截图正常，妖王监测继续运行。"
    return (f"游戏画面已恢复刷新（此前冻结 {_fmt_duration(frozen_seconds)}），"
            f"妖王监测继续运行。")


def classify(*, guard_state: str, connected, screencap_fails: int,
             frozen_seconds: float) -> str:
    """合成最终链路判定：控制器明确断连 + 旁证 → 'disconnected'，否则用守卫状态。

    ``connected`` 传 None 表示当前 MaaFramework 拿不到该属性（老版本），此时不看断连证据。
    """
    if connected is False and (
        screencap_fails >= DISCONNECT_CONFIRM_FAILS
        or frozen_seconds >= DISCONNECT_CONFIRM_FROZEN_SECONDS
    ):
        return STATE_DISCONNECTED
    return guard_state


class LinkGuard:
    """按轮复用的链路守卫。

    轮内计数用 :meth:`reset_round` 清空，告警时间戳跨轮保留 —— 否则每轮（几十秒）
    都会重新告警一次，把 Telegram 刷屏。
    """

    def __init__(self, freeze_seconds=DEFAULT_FREEZE_SECONDS,
                 screencap_fail_limit=DEFAULT_SCREENCAP_FAIL_LIMIT,
                 alert_cooldown=DEFAULT_ALERT_COOLDOWN_SECONDS):
        self.configure(freeze_seconds, screencap_fail_limit, alert_cooldown)
        self.last_alert_ts = 0.0
        self.last_alert_state = STATE_OK
        self.alert_count = 0
        self.frozen_total = 0.0  # 最近一次告警时的冻结时长，用于恢复通知
        self.reset_round()

    def configure(self, freeze_seconds=None, screencap_fail_limit=None, alert_cooldown=None):
        """按当前配置刷新阈值（analyze 每轮都会用参数覆盖一次）。"""
        if freeze_seconds is not None:
            self.freeze_seconds = max(1.0, float(freeze_seconds))
        if screencap_fail_limit is not None:
            self.screencap_fail_limit = max(1, int(screencap_fail_limit))
        if alert_cooldown is not None:
            self.alert_cooldown = max(0.0, float(alert_cooldown))

    def reset_round(self):
        """新的一轮开始：只清轮内计数，告警控频状态保留。"""
        self._frozen_since = None
        self._screencap_fails = 0
        self._changed_streak = 0

    # ---------------- 帧 / 截图回调 ----------------
    def on_frame(self, changed: bool, now: float):
        """记录一帧：changed 为 False 时进入/延续"冻结"计时。"""
        if changed:
            self._frozen_since = None
            self._changed_streak += 1
        else:
            if self._frozen_since is None:
                self._frozen_since = float(now)
            self._changed_streak = 0

    def on_screencap_ok(self, now: float):
        self._screencap_fails = 0

    def on_screencap_fail(self, now: float):
        self._screencap_fails += 1

    # ---------------- 状态查询 ----------------
    def frozen_seconds(self, now: float) -> float:
        if self._frozen_since is None:
            return 0.0
        return max(0.0, float(now) - self._frozen_since)

    @property
    def screencap_fails(self) -> int:
        return self._screencap_fails

    def state(self, now: float) -> str:
        """当前链路状态：'' / 'screencap' / 'frozen'（截图连续失败优先）。"""
        if self._screencap_fails >= self.screencap_fail_limit:
            return STATE_SCREENCAP
        if self.frozen_seconds(now) >= self.freeze_seconds:
            return STATE_FROZEN
        return STATE_OK

    # ---------------- 告警控频 ----------------
    def should_alert(self, state: str, now: float) -> bool:
        if not state:
            return False
        now = float(now)
        # 任意两条告警之间的硬下限（防不同故障类型来回刷）
        if self.alert_count and (now - self.last_alert_ts) < MIN_ALERT_GAP_SECONDS:
            return False
        if state != self.last_alert_state:
            return True  # 换了种故障 → 立即告警
        return (now - self.last_alert_ts) >= self.alert_cooldown

    def mark_alerted(self, state: str, now: float, frozen_seconds: float = 0.0):
        self.last_alert_ts = float(now)
        self.last_alert_state = state
        self.alert_count += 1
        if state == STATE_FROZEN:
            self.frozen_total = max(self.frozen_total, float(frozen_seconds))

    def needs_recovery_notice(self, state: str) -> bool:
        """告警过、且画面确实持续在刷新 → 需要补一条恢复通知。

        要求连续 ``RECOVER_CONFIRM_FRAMES`` 帧真有变化：掉线那一刻画面是旧帧，
        下一轮第一帧必然"看起来变了"，只看一帧会误报「已恢复」。
        """
        return (
            (not state)
            and self.last_alert_state != STATE_OK
            and self._changed_streak >= RECOVER_CONFIRM_FRAMES
        )

    def mark_recovered(self):
        self.last_alert_state = STATE_OK
        self.frozen_total = 0.0
