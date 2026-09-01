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
"""

import json
import time
import os

import numpy as np

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition
from maa.context import Context

from utils import logger
from utils import send_message

# 独立的小体积妖王调试日志（只写 yaowang 相关行，方便直接发送排查，
# 避免 app.log 过大无法上传）。若无 loguru 则降级为仅 console。
try:
    from loguru import logger as _ylogger

    _ylog_path = os.path.join(os.getcwd(), "debug", "custom", "yaowang.log")
    try:
        os.makedirs(os.path.dirname(_ylog_path), exist_ok=True)
        _ylogger.remove()
        _ylogger.add(
            _ylog_path,
            rotation="1 day",
            retention="3 days",
            level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}",
            encoding="utf-8",
            enqueue=True,
            backtrace=True,
            diagnose=True,
        )
        _ylog = _ylogger
    except Exception:
        _ylog = None
except ImportError:
    _ylog = None


def _ylog_info(msg: str):
    logger.info(f"[yaowang] {msg}")
    if _ylog is not None:
        try:
            _ylog.info(msg)
        except Exception:
            pass


def _ylog_warn(msg: str):
    logger.warning(f"[yaowang] {msg}")
    if _ylog is not None:
        try:
            _ylog.warning(msg)
        except Exception:
            pass


def _ylog_err(msg: str):
    logger.error(f"[yaowang] {msg}")
    if _ylog is not None:
        try:
            _ylog.error(msg)
        except Exception:
            pass


def _ylog_debug(msg: str):
    logger.debug(f"[yaowang] {msg}")
    if _ylog is not None:
        try:
            _ylog.debug(msg)
        except Exception:
            pass

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

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult:
        param: dict = json.loads(argv.custom_recognition_param or "{}")

        enabled = param.get("yaowang_enabled", True)
        rx = float(param.get("yaowang_chat_ratio_x", DEFAULT_CHAT_RATIO_X))
        ry = float(param.get("yaowang_chat_ratio_y", DEFAULT_CHAT_RATIO_Y))
        cooldown = int(param.get("yaowang_cooldown_seconds", DEFAULT_COOLDOWN))
        frame_interval = float(param.get("yaowang_frame_interval", DEFAULT_FRAME_INTERVAL))
        frame_diff_thr = float(param.get("yaowang_frame_diff", DEFAULT_FRAME_DIFF))
        expected: list = param.get("yaowang_expected", DEFAULT_EXPECTED)
        exclude: list = param.get("yaowang_exclude", DEFAULT_EXCLUDE)

        _ylog_info(
            "[yaowang] ===== 蹲妖王识别轮开始 ===== 参数: "
            f"enabled={enabled} rx={rx} ry={ry} cooldown={cooldown}s "
            f"interval={frame_interval}s diff_thr={frame_diff_thr} "
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
            # 先截图获取尺寸
            image0 = context.tasker.controller.post_screencap().wait().get()
            try:
                h, w = image0.shape[:2]
            except Exception:
                _ylog_err("[yaowang] 无法获取截图尺寸，跳过。")
                return CustomRecognition.AnalyzeResult(box=None, detail="截图异常")
            rois = self._chat_rois(h, w, rx, ry)
            _ylog_info(f"[yaowang] 截图尺寸 {w}x{h}，系统公告栏 ROI: {rois}")

        # 高扫描次数上限，避免单次 analyze 无限循环占死
        max_scan = int(param.get("yaowang_max_scan", 200))
        # 兜底 OCR 周期：连续多少帧无变化才强制 OCR 一次，确保"静止的妖王公告"也能被识别。
        # 帧差方案对"滚动消息"有效，但若妖王公告静止停留、_LAST_FRAME 已缓存含妖王帧，
        # 帧差会一直是 0 永不再触发 OCR → 漏检。加周期兜底可解决。
        force_ocr_every = int(param.get("yaowang_force_ocr_every", 6))
        _ylog_info(
            f"[yaowang] 本轮最大扫描 {max_scan} 次，无变化每 {force_ocr_every} 帧强制 OCR 一次（兜底防漏静止公告）。"
        )

        no_change_count = 0  # 连续无变化的帧计数
        for scan in range(max_scan):
            image = context.tasker.controller.post_screencap().wait().get()
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
                    if ok:
                        self._LAST_NOTIFY_TS = now
                else:
                    _ylog_info(f"[yaowang] ❌ 仍在冷却期内，静默跳过本次通知（避免刷屏）")
                return CustomRecognition.AnalyzeResult(
                    box=hit_box, detail=f"识别到妖王出现：{hit_word}"
                )

            time.sleep(frame_interval)

        _ylog_warn("[yaowang] 本轮达到扫描上限，未识别到妖王（会由外部再次调度）")
        return CustomRecognition.AnalyzeResult(box=None, detail="本轮未识别到妖王（已达扫描上限）")