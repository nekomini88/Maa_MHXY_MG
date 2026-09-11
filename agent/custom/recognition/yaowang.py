# -*- coding: utf-8 -*-
"""妖王出现识别 + 通知（帧差触发 + OCR 确认）。

自定义识别器 ``yaowang``：持续监测屏幕左侧【系统公告栏】（聊天栏）的妖王出现公告，
并在检测到后推送 Telegram 通知。

解决的问题：妖王公告在聊天栏停留时间短、且不断被后续系统消息 flush 顶走，
而 OCR 单轮需约 3 秒——若按每 3 秒 OCR 一次的策略，极易漏掉闪现的公告。

方案：识别器在本轮 analyze() 内【自循环高频截图 + 帧差检测】：
  1. 对系统公告栏 ROI 截图，与上一帧做像素差；帧差计算本身是亚毫秒级。
  2. 只有当区域内容【发生变化】（有新公告进来/滚动）时才对该帧做 OCR 确认；
     无变化的帧只截图不做 OCR，避免每轮都付出 3 秒 OCR 代价。
  3. OCR 命中【妖王出现类】公告 → 通知；命中【战胜/结束类】→ 静默排除。

公告形态（实测）：
【出现类 - 需通知】
  「佟得来在挖宝时放出了远古妖王」
  「妖魔冲到了建邺城（25级可挑战），大家快去击败妖王啊！」

【战胜/结束类 - 需排除】
  「加加、战胜了妖王，妖王留下一个定魂珠后一溜烟的跑了！」
  → 含「战胜」等结束标志时不通知。

聊天栏位置（挂机主界面，横屏模拟器）：x≈16~45% 宽度，y≈18%~100% 高度。

配置（config/config.json）：
    yaowang_enabled:          是否启用（默认 true）
    yaowang_rois:             自定义 OCR/帧差 ROI 列表 [[x,y,w,h]]（默认按比例计算）
    yaowang_chat_ratio_x:     聊天栏占全图宽比例（默认 0.45）
    yaowang_chat_ratio_y:     聊天栏顶部占全图高比例（默认 0.18）
    yaowang_frame_interval:   帧差扫描间隔秒（默认 0.25，受截图速度限制）
    yaowang_frame_diff:       判定"变化"的像素差阈值（默认 3000）
    yaowang_cooldown_seconds: 两次通知最小间隔秒数（默认 60）
    yaowang_expected:         出现类匹配词（默认含"妖王""远古妖王""妖魔冲到了"）
    yaowang_exclude:          结束类排除词（默认含"战胜""已被击败""剿灭""被击杀"）
    yaowang_max_scan:         单轮最大扫描次数（默认 200；达到后本轮干净收尾，交由管线续跑）
    yaowang_freeze_seconds:   画面连续静止多少秒判定截图链路失效（默认 180）
    yaowang_screencap_fail_limit: 连续截图失败多少次判定链路失效（默认 30）
    yaowang_link_alert_cooldown:  同类链路故障的最小告警间隔秒（默认 1800）

链路失效处理（"总是跑一段时间就失败、不能一直监控"的修复）：
  模拟器/ADB 掉线后 MaaFramework 会丢弃新的截图请求，画面会一直停在最后一帧。
  旧实现对此毫无反应 —— 单轮盲跑 49 分钟才到扫描上限，随后管线去执行"必须新截图"
  的收尾节点，超时 → 整条任务被判 ``Tasker.Task.Failed``，监测就此停摆。
  现在：控制器报告断连（``MaaControllerConnected=false``，另有截图失败/画面静止旁证）、
  画面冻结 ``yaowang_freeze_seconds`` 秒、或截图连续失败
  ``yaowang_screencap_fail_limit`` 次 → 发一条 Telegram 告警 + 主动尝试重连，
  并立刻结束本轮（不再空跑），下一轮重新取帧；画面恢复后再补一条"已恢复"。
  Telegram 文字发不出去时不阻塞监测（只在日志里记一笔）。
"""

import json
import time
import os
from typing import Any

import numpy as np

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition
from maa.context import Context

from utils import logger
from utils import log_policy
from utils import link_guard
from utils import send_message

# 妖王专用日志：单独一个文件，只收 [yaowang] 消息，方便整份发给维护者排查。
#
# 不要再写 `from loguru import logger as _ylogger; _ylogger.remove()`：
# loguru 是单例，remove() 会把 utils/logger.py 配好的 console / 每日日志 sink
# 一并干掉（之后整个项目的日志都只落进 yaowang.log），而且 helpers 里再调一次
# _ylog.info() 会把同一条消息写两遍。这里只在现有 logger 上追加一个带 filter
# 的 sink，全部日志仍由 utils.logger 统一出口。
_YAOWANG_LOG_PATH = os.path.join("debug", "custom", "yaowang.log")
# 单文件超过 20 MB 轮转、轮转后压缩、只保留 7 天（口径统一在 utils/log_policy.py）
_YAOWANG_LOG_SINK = log_policy.sink_kwargs()


def _install_yaowang_sink() -> bool:
    """给全局 logger 追加一个只收 '[yaowang]' 消息的文件 sink。"""
    try:
        os.makedirs(os.path.dirname(_YAOWANG_LOG_PATH), exist_ok=True)
        logger.add(
            _YAOWANG_LOG_PATH,
            **_YAOWANG_LOG_SINK,
            level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}",
            encoding="utf-8",
            enqueue=True,
            backtrace=True,
            diagnose=True,
            filter=lambda record: "[yaowang]" in record["message"],
        )
        return True
    except Exception:  # loguru 缺失或目录不可写时，退化为只走主日志
        return False


_YAOWANG_SINK_OK = _install_yaowang_sink()


def _ylog_fmt(msg) -> str:
    """统一补 [yaowang] 前缀，且只补一次（调用方有的带前缀、有的不带）。"""
    text = str(msg)
    return text if text.startswith("[yaowang]") else f"[yaowang] {text}"


def _ylog_info(msg: str):
    logger.info(_ylog_fmt(msg))


def _ylog_warn(msg: str):
    logger.warning(_ylog_fmt(msg))


def _ylog_err(msg: str):
    logger.error(_ylog_fmt(msg))


def _controller_connected(context: Context) -> Any:
    """控制器是否已连接（拿不到该属性时返回 None，调用方按"未知"处理）。"""
    controller = getattr(getattr(context, "tasker", None), "controller", None)
    connected = getattr(controller, "connected", None)
    return connected if isinstance(connected, bool) else None


def _link_state(context: Context, now: float) -> str:
    """本轮当前链路判定：控制器断连证据 + 守卫状态（截图连续失败 / 画面冻结）。"""
    return link_guard.classify(
        guard_state=_LINK_GUARD.state(now),
        connected=_controller_connected(context),
        screencap_fails=_LINK_GUARD.screencap_fails,
        frozen_seconds=_LINK_GUARD.frozen_seconds(now),
    )


def _ylog_debug(msg: str):
    logger.debug(_ylog_fmt(msg))


# 截图链路守卫：轮内计数每轮清空，告警控频状态跨轮保留（否则每轮都会重发告警）。
_LINK_GUARD = link_guard.LinkGuard()


def _tasker_stopping(context: Context) -> bool:
    """任务是否已被请求停止（MFA 点停止 / 切换任务）。

    没有这个检查时，停止请求要等本轮扫描上限才生效 —— 正常约 1 分钟，
    断链时可能拖到几十分钟。老版本 MaaFramework 没有 stopping 属性 → getattr 兜底。

    只看 stopping 标志，不看 tasker.running：万一某个版本在识别回调里把 running
    报成 False，按 running 判断会让每一轮都立刻"正常收尾"，监测静默停摆（比不检查更糟）。
    """
    return bool(getattr(getattr(context, "tasker", None), "stopping", False))


# 系统公告栏 ROI 默认参数（比例，随截图尺寸换算）
DEFAULT_CHAT_RATIO_X = 0.45   # 聊天栏宽度占全图宽
DEFAULT_CHAT_RATIO_Y = 0.18   # 聊天栏顶部占全图高
DEFAULT_COOLDOWN = 60         # 秒
DEFAULT_FRAME_INTERVAL = 0.25 # 帧差扫描间隔秒
DEFAULT_FRAME_DIFF = 3000     # 判定"变化"的像素差阈值
# 出现类关键词（含容错变体）：妖王/远古妖王/妖魔冲到了
DEFAULT_EXPECTED = ["妖王", "远古妖王", "妖魔冲到了", "妖玉", "妖互"]
# 结束类排除词（含"战胜"等明确结束标志；注意不能含"击败"——"去击败妖王啊"是出现号召）
DEFAULT_EXCLUDE = ["战胜", "已被击败", "已击败", "剿灭", "被击杀", "被击败"]
# 截图链路失效判定与告警控频（口径集中在 utils/link_guard.py）
DEFAULT_FREEZE_SECONDS = link_guard.DEFAULT_FREEZE_SECONDS
DEFAULT_SCREENCAP_FAIL_LIMIT = link_guard.DEFAULT_SCREENCAP_FAIL_LIMIT
DEFAULT_LINK_ALERT_COOLDOWN = link_guard.DEFAULT_ALERT_COOLDOWN_SECONDS


@AgentServer.custom_recognition("yaowang")
class Yaowang(CustomRecognition):
    """帧差触发 + OCR 确认 识别系统公告栏的「妖王出现」公告并通知。"""

    _ROC_NAME = "yaowang-ocr"
    _LAST_NOTIFY_TS = 0.0       # 最近一次通知时间戳（防止连发刷屏）
    _LAST_FRAME = None          # 上一帧截图（二维灰度数组，用于帧差）

    @classmethod
    def _chat_rois(cls, image_h: int, image_w: int, rx: float, ry: float) -> list:
        """按实际截图尺寸计算系统公告栏 ROI（比例定位，兼容不同模拟器分辨率）。"""
        x1 = int(image_w * rx)
        y0 = int(image_h * ry)
        return [[0, y0, x1, image_h - y0]]

    @staticmethod
    def _roi_gray(image, roi) -> np.ndarray:
        """从整图按 roi 裁出灰度图。"""
        x, y, w, h = roi
        sub = image[y:y + h, x:x + w]
        # 转为灰度（若为彩色）
        if sub.ndim == 3:
            # RGB/灰度加权
            sub = sub[:, :, 0] * 0.299 + sub[:, :, 1] * 0.587 + sub[:, :, 2] * 0.114
        return sub.astype(np.float32)

    @staticmethod
    def _frame_diff(prev, cur) -> float:
        """计算两帧灰度差异（绝对值差的总和）。"""
        if prev is None or prev.shape != cur.shape:
            return float("inf")  # 形状变化视为"变化"
        return float(np.abs(prev - cur).sum())

    def _should_notify(self, text: str, expected: list, exclude: list) -> bool:
        """判断命中文本是否属于"妖王出现"且非"战胜/结束"。"""
        if not any(w in text for w in expected) and "妖魔冲到了" not in text:
            return False
        # 命中妖王关键词，但若是结束公告则排除
        if any(w in text for w in exclude):
            return False
        return True

    @staticmethod
    def _try_reconnect(context: Context, tag: str) -> bool:
        """链路失效时主动请求控制器重连（拿不到 API / 失败都只记日志，不影响监测）。"""
        controller = getattr(getattr(context, "tasker", None), "controller", None)
        post_connection: Any = getattr(controller, "post_connection", None)
        if not callable(post_connection):
            return False
        try:
            post_connection().wait()
            _ylog_warn(f"[yaowang] {tag}：已请求控制器重连（post_connection）。")
            return True
        except Exception as e:
            _ylog_warn(f"[yaowang] {tag}：控制器重连请求失败（{e}），继续等待下一轮重试。")
            return False

    def _alert_link_loss(self, context: Context, state: str, *,
                         frozen_seconds: float = 0.0, screencap_fails: int = 0,
                         last_error: str = "") -> None:
        """链路失效：控频发 TG 告警 + 尝试重连。

        告警是否发得出去都不阻塞监测；发送结果只记日志（配置缺失时 send_message
        自己会打出 [message] 层的原因）。
        """
        now = time.time()
        if _LINK_GUARD.should_alert(state, now):
            ok = send_message(
                link_guard.ALERT_TITLE,
                link_guard.alert_text(
                    state,
                    frozen_seconds=frozen_seconds,
                    screencap_fails=screencap_fails,
                    last_error=last_error,
                ),
            )
            _LINK_GUARD.mark_alerted(state, now, frozen_seconds)
            _ylog_warn(
                f"[yaowang] 链路失效告警（{link_guard.state_text(state)}）发送结果: "
                f"{'成功 ✅' if ok else '失败 ❌（监测继续，详见 [message] 日志）'}"
            )
        else:
            _ylog_debug(
                f"[yaowang] 链路仍然失效（{link_guard.state_text(state)}），"
                f"处于告警冷却期（{_LINK_GUARD.alert_cooldown:.0f}s），不重复发送。"
            )
        self._try_reconnect(context, link_guard.state_text(state))

    def _notice_recovered(self, context: Context, state: str) -> None:
        """之前告警过、现在画面又刷新了 → 补一条"已恢复"，避免用户以为还在告警。"""
        if not _LINK_GUARD.needs_recovery_notice(state):
            return
        alerted_state = _LINK_GUARD.last_alert_state
        ok = send_message(
            link_guard.RECOVERY_TITLE,
            link_guard.recovery_text(
                frozen_seconds=_LINK_GUARD.frozen_total,
                alerted_state=alerted_state,
            ),
        )
        _LINK_GUARD.mark_recovered()
        _ylog_info(
            f"[yaowang] 截图链路已恢复（此前 {link_guard.state_text(alerted_state)}），"
            f"恢复通知发送结果: {'成功 ✅' if ok else '失败 ❌'}"
        )

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult:
        # 健壮解析 custom_recognition_param：防 None/非字符串/非法JSON。
        # 日志曾报 'NoneType' object has no attribute 'get'（param=None），
        # 此处兜底确保 param 必定是 dict，任何异常都不会让 analyze 崩溃。
        raw = getattr(argv, "custom_recognition_param", None) or "{}"
        if not isinstance(raw, str):
            raw = "{}"
        try:
            param = json.loads(raw)
        except Exception:
            param = {}
        if not isinstance(param, dict):
            param = {}

        enabled = param.get("yaowang_enabled", True)
        rx = float(param.get("yaowang_chat_ratio_x", DEFAULT_CHAT_RATIO_X))
        ry = float(param.get("yaowang_chat_ratio_y", DEFAULT_CHAT_RATIO_Y))
        cooldown = int(param.get("yaowang_cooldown_seconds", DEFAULT_COOLDOWN))
        frame_interval = float(param.get("yaowang_frame_interval", DEFAULT_FRAME_INTERVAL))
        frame_diff_thr = float(param.get("yaowang_frame_diff", DEFAULT_FRAME_DIFF))
        expected: list = param.get("yaowang_expected", DEFAULT_EXPECTED)
        exclude: list = param.get("yaowang_exclude", DEFAULT_EXCLUDE)
        # 截图链路守卫参数（防"跑一段时间就失败"）：画面冻结 / 截图连续失败 → 告警 + 提前收尾
        freeze_seconds = float(param.get("yaowang_freeze_seconds", DEFAULT_FREEZE_SECONDS))
        screencap_fail_limit = int(
            param.get("yaowang_screencap_fail_limit", DEFAULT_SCREENCAP_FAIL_LIMIT)
        )
        link_alert_cooldown = float(
            param.get("yaowang_link_alert_cooldown", DEFAULT_LINK_ALERT_COOLDOWN)
        )
        _LINK_GUARD.configure(
            freeze_seconds=freeze_seconds,
            screencap_fail_limit=screencap_fail_limit,
            alert_cooldown=link_alert_cooldown,
        )

        _ylog_info(
            "[yaowang] ===== 蹲妖王识别轮开始 ===== 参数: "
            f"enabled={enabled} rx={rx} ry={ry} cooldown={cooldown}s "
            f"interval={frame_interval}s diff_thr={frame_diff_thr} "
            f"freeze={freeze_seconds:.0f}s cap_fail={screencap_fail_limit} "
            f"link_alert_cooldown={link_alert_cooldown:.0f}s "
            f"expected={expected} exclude={exclude}"
        )

        if not enabled:
            _ylog_info("[yaowang] yaowang_enabled=false，跳过蹲妖王识别。")
            return CustomRecognition.AnalyzeResult(box=None, detail="已禁用蹲妖王")

        custom_rois = param.get("yaowang_rois")
        if custom_rois:
            rois = custom_rois
            _ylog_info(f"[yaowang] 使用自定义 ROI: {rois}")
        else:
            # 先截图获取尺寸（多开切换时也可能失败, 记日志并降级返回, 不崩任务）
            try:
                image0 = context.tasker.controller.post_screencap().wait().get()
                h, w = image0.shape[:2]
            except Exception as e:
                _ylog_warn(f"[yaowang] 初始截图失败（{e}），无法确定 ROIs，本轮降级跳过。")
                return CustomRecognition.AnalyzeResult(box=None, detail="初始截图异常")
            rois = self._chat_rois(h, w, rx, ry)
            _ylog_info(f"[yaowang] 截图尺寸 {w}x{h}，系统公告栏 ROI: {rois}")

        # 单轮扫描上限：到点就干净收尾（返回未命中），由流水线 next 立刻续下一轮，监测不中断。
        max_scan = int(param.get("yaowang_max_scan", 200))
        # 兜底 OCR 周期：连续多少帧无变化才强制 OCR 一次，确保"静止的妖王公告"也能被识别。
        # 帧差方案对"滚动消息"有效，但若妖王公告静止停留、_LAST_FRAME 已缓存含妖王帧，
        # 帧差会一直是 0 永不再触发 OCR → 漏检。加周期兜底可解决。
        force_ocr_every = int(param.get("yaowang_force_ocr_every", 6))
        _ylog_info(
            f"[yaowang] 本轮最大扫描 {max_scan} 次，无变化每 {force_ocr_every} 帧强制 OCR 一次（兜底防漏静止公告）；"
            f"画面连续静止 {freeze_seconds:.0f}s 或截图连续失败 {screencap_fail_limit} 次即告警并提前收尾。"
        )

        no_change_count = 0  # 连续无变化的帧计数
        # 新的一轮：只清轮内计数，告警控频（冷却时间戳）跨轮保留，避免每轮重复告警。
        _LINK_GUARD.reset_round()
        for scan in range(max_scan):
            # 任务已请求停止（MFA 点停止/切任务）→ 立即干净退出，不等扫描上限。
            if _tasker_stopping(context):
                _ylog_info(f"[yaowang] 任务已请求停止，第{scan}次扫描时结束本轮。")
                return CustomRecognition.AnalyzeResult(box=None, detail="任务已停止")

            # 截图可能因多开切换/模拟器最小化而失败(脱钩)。此处捕获, 短暂等待重试,
            # 避免整个 analyze 抛异常导致 MaaFramework 判"任务失败"。
            now = time.time()
            try:
                image = context.tasker.controller.post_screencap().wait().get()
                if image is None:
                    raise RuntimeError("post_screencap 返回 None")
            except Exception as e:
                _LINK_GUARD.on_screencap_fail(now)
                cap_fails = _LINK_GUARD.screencap_fails
                link_state = _link_state(context, now)
                if scan % 5 == 0 or link_state:
                    _ylog_warn(
                        f"[yaowang] 第{scan}次截图失败（{e}），已连续失败 {cap_fails} 次，重试中..."
                    )
                if link_state:
                    _ylog_warn(
                        f"[yaowang] 判定截图链路失效（{link_guard.state_text(link_state)}："
                        f"连续截图失败 {cap_fails} 次 / 上限 {screencap_fail_limit}）→ "
                        f"告警并提前收尾；下一轮会重新取帧，链路恢复后监测自动继续。"
                    )
                    self._alert_link_loss(
                        context, link_state, screencap_fails=cap_fails, last_error=str(e)
                    )
                    return CustomRecognition.AnalyzeResult(
                        box=None, detail="截图链路失效，本轮告警收尾"
                    )
                time.sleep(frame_interval)
                continue
            _LINK_GUARD.on_screencap_ok(now)

            changed = False
            change_roi = rois[0]
            last_diff = 0.0
            # 帧差：对每个 ROI 检测是否变化（仅作加速信号，不强制依赖）
            for roi in rois:
                cur = self._roi_gray(image, roi)
                diff = self._frame_diff(self._LAST_FRAME, cur)
                last_diff = diff
                if diff > frame_diff_thr:
                    changed = True
                    change_roi = roi
                    break

            # 更新基准帧（只保存第一个 ROI 的灰度，降低内存）
            self._LAST_FRAME = self._roi_gray(image, rois[0])

            # 链路守卫：连续静止累计到阈值 → 判定"截图链路失效"（画面冻结），
            # 告警 + 提前收尾，不再像旧版那样盲跑到扫描上限（实测盲跑过 49 分钟）。
            _LINK_GUARD.on_frame(changed, now)
            link_state = _link_state(context, now)
            if link_state:
                frozen = _LINK_GUARD.frozen_seconds(now)
                _ylog_warn(
                    f"[yaowang] 判定截图链路失效（{link_guard.state_text(link_state)}："
                    f"画面已连续静止 {frozen:.0f}s / 阈值 {freeze_seconds:.0f}s，"
                    f"连续 {no_change_count} 帧 diff={last_diff:.0f}）→ "
                    f"告警并提前收尾；下一轮重新取帧，链路恢复后监测自动继续。"
                )
                self._alert_link_loss(context, link_state, frozen_seconds=frozen)
                return CustomRecognition.AnalyzeResult(
                    box=None, detail="画面冻结，本轮告警收尾"
                )
            # 之前告警过、现在画面又刷新了 → 补一条"已恢复"，避免用户以为还在告警。
            self._notice_recovered(context, link_state)

            # 触发条件：区域变化（加速）OR 连续无变化达周期（兜底静止公告）
            if changed:
                no_change_count = 0
                should_ocr = True
            else:
                no_change_count += 1
                should_ocr = no_change_count >= force_ocr_every

            if not should_ocr:
                if scan % 20 == 0:
                    _ylog_debug(f"[yaowang] 第{scan}次扫描：公告栏无变化 diff={last_diff:.0f}，继续监测")
                time.sleep(frame_interval)
                continue

            log_reason = "公告栏变化" if changed else f"连续{no_change_count}帧静止(兜底OCR)"
            _ylog_info(
                f"[yaowang] 触发 OCR（{log_reason} diff={last_diff:.0f}）"
            )

            # 有变化 → OCR 确认
            hit_box = None
            hit_word = None
            full_text = ""
            try:
                reco = context.run_recognition(
                    self._ROC_NAME,
                    image,
                    pipeline_override={
                        self._ROC_NAME: {
                            "recognition": "OCR",
                            "roi": change_roi,
                            "expected": expected,
                            "threshold": 0.6,
                        }
                    },
                )
            except Exception as e:
                _ylog_warn(f"[yaowang] OCR 调用异常（{e}），本轮跳过。")
                time.sleep(frame_interval)
                continue
            if reco and reco.hit and reco.all_results:
                texts = [r.text for r in reco.all_results if r.text]
                full_text = " ".join(texts)
                _ylog_info(f"[yaowang] OCR 命中 {len(texts)} 条文本: {full_text[:80]}")
                for r in reco.all_results:
                    if not r.text:
                        continue
                    notify = self._should_notify(r.text, expected, exclude)
                    if notify:
                        if hit_box is None:
                            hit_box = r.box
                            hit_word = r.text
                        _ylog_info(f"[yaowang]   内容含妖王特征且非战胜 → 应通知: {r.text[:50]}")
                    else:
                        _ylog_debug(f"[yaowang]   已过滤(非妖王或战胜): {r.text[:50]}")
            elif reco is None or not reco.hit:
                _ylog_info("[yaowang] OCR 未命中任何关键词（此轮公告栏变化但无妖王文本）")

            if hit_box:
                _ylog_info(f"[yaowang] ✅ 识别到妖王出现公告：{hit_word}")
                now = time.time()
                elapsed = now - self._LAST_NOTIFY_TS
                _ylog_info(f"[yaowang] 距上次通知 {elapsed:.1f}s（冷却要求 {cooldown}s）")
                # 冷却：仅在满足间隔且发送实际成功时才更新通知时间戳。
                # 若发送失败（网络/配置问题），不更新 _LAST_NOTIFY_TS，下次命中仍会重试，
                # 避免"妖王出现却被静默吞掉"。
                if now - self._LAST_NOTIFY_TS >= cooldown:
                    content = hit_word or full_text or "妖王出现"
                    _ylog_info(f"[yaowang] 满足冷却，发送通知: {content[:60]}")
                    ok = send_message("妖王出现", content)
                    _ylog_info(f"[yaowang] 通知发送结果: {'成功 ✅' if ok else '失败 ❌（send_message返回False）'}")
                    if not ok:
                        # 发送失败时，主动抓取 message 模块的失败原因写入 yaowang.log，
                        # 便于直接定位是"token为空/config没读到"还是"HTTP失败"。
                        try:
                            from utils import message as _msg_mod
                            _msg_mod.read_config()
                            _cfg = _msg_mod.config or {}
                            _en = _cfg.get("ExternalNotificationEnabled", "")
                            _tk_raw = str(_cfg.get("ExternalNotificationTelegramBotToken", "") or "")
                            _ci_raw = str(_cfg.get("ExternalNotificationTelegramChatId", "") or "")
                            try:
                                from utils import notify_config as _nc
                            except ImportError:  # 兜底：按包路径导入
                                import utils.notify_config as _nc  # type: ignore

                            # config/config.json 是 MFAAvalonia 的 Default 配置本体，
                            # 里面的 token/chat_id 可能是 MFA 加密值 → 这里按实际
                            # 生效值（能解密就解密）来判断，而不是拿密文长度误导排查。
                            _tk, _tk_src = _nc.normalize_value(_tk_raw)
                            _ci, _ci_src = _nc.normalize_value(_ci_raw)
                            _ylog_err(
                                "[yaowang] 发送失败诊断: ExternalNotificationEnabled="
                                f"{_en} | token={_nc.mask(_tk)} [{_tk_src}]"
                                f" | chat_id={_nc.mask(_ci)} [{_ci_src}]"
                            )
                            _problems = _nc.validate(_tk, _ci)
                            for _i, _p in enumerate(_problems, 1):
                                _ylog_err(f"[yaowang] 配置问题 {_i}: {_p}")
                            _hint = _nc.mfa_hint(_tk_raw, _ci_raw)
                            if _hint:
                                _ylog_err(f"[yaowang] {_hint}")
                            if not _problems:
                                _ylog_err(
                                    "[yaowang] 配置格式没问题 → 失败原因在网络或 Telegram 侧，"
                                    "请看上一行 [message] 的状态码与原因解读。"
                                )
                        except Exception as _e:
                            _ylog_err(f"[yaowang] 读取诊断信息失败: {_e}")
                    if ok:
                        self._LAST_NOTIFY_TS = now
                else:
                    _ylog_info(f"[yaowang] ❌ 仍在冷却期内，静默跳过本次通知（避免刷屏）")
                return CustomRecognition.AnalyzeResult(
                    box=hit_box, detail=f"识别到妖王出现：{hit_word}"
                )

            time.sleep(frame_interval)

        _ylog_info(
            "[yaowang] 本轮达到扫描上限，未识别到妖王，正常收尾（管线 next 会立刻续下一轮，监测不中断）"
        )
        return CustomRecognition.AnalyzeResult(box=None, detail="本轮未识别到妖王（已达扫描上限）")