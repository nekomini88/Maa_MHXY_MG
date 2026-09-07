# -*- coding: utf-8 -*-
"""竞技场挂机识别 + 点击（CustomRecognition 供 pipeline 调用）。

自定义识别器 ``jingjichang``：监测「竞技场」面板右下角的「开始匹配」
按钮，出现即返回命中框，由 pipeline 节点执行 Click 点下，点完循环
继续蹲（挂机），直到任务停止或超时。

按钮位置（横屏模拟器，比例定位兼容不同分辨率）：
  实测 957x521 截图：按钮约 x 775~835，y 415~500。
  默认 ROI 取相对比例 [0.76, 0.75, 0.18, 0.23]，覆盖按钮并留边距。

配置（pipeline 节点 custom_recognition_param 传入）：
    jjc_enabled:           是否启用（默认 true）
    jjc_roi:               自定义 ROI [x,y,w,h] 绝对像素（默认按比例计算）
    jjc_roi_ratio:         默认 ROI 比例 [x,y,w,h]（默认 [0.76,0.75,0.18,0.23]）
    jjc_expected:          匹配词（默认 ["开始匹配","开始匹","始匹配"]）
    jjc_max_wait:          单轮 analyze 最长等待秒数（默认 600，打完一场的时间）
    jjc_interval:          轮询截图间隔秒（默认 2.0，挂机场景不必太密）
    jjc_notify:            命中是否发通知（默认 false，挂机不打扰）
"""

import json
import time

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition
from maa.context import Context

from utils import logger
from utils import send_message

DEFAULT_ROI_RATIO = [0.76, 0.75, 0.18, 0.23]
DEFAULT_EXPECTED = ["开始匹配", "开始匹", "始匹配"]
DEFAULT_MAX_WAIT = 600
DEFAULT_INTERVAL = 2.0


@AgentServer.custom_recognition("jingjichang")
class Jingjichang(CustomRecognition):
    """识别「开始匹配」按钮，命中返回框供 pipeline 点击（循环挂机）。"""

    _ROC_NAME = "jingjichang-ocr"
    _LAST_NOTIFY_TS = 0.0

    @staticmethod
    def _default_roi(h: int, w: int, ratio: list) -> list:
        rx, ry, rw, rh = ratio
        return [int(w * rx), int(h * ry), int(w * rw), int(h * rh)]

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult:
        raw = getattr(argv, "custom_recognition_param", None) or "{}"
        if not isinstance(raw, str):
            raw = "{}"
        try:
            param = json.loads(raw)
        except Exception:
            param = {}
        if not isinstance(param, dict):
            param = {}

        enabled = param.get("jjc_enabled", True)
        if not enabled:
            return CustomRecognition.AnalyzeResult(box=None, detail="竞技场挂机已禁用")

        expected = param.get("jjc_expected", DEFAULT_EXPECTED)
        max_wait = float(param.get("jjc_max_wait", DEFAULT_MAX_WAIT))
        interval = float(param.get("jjc_interval", DEFAULT_INTERVAL))
        notify = bool(param.get("jjc_notify", False))

        roi = param.get("jjc_roi")
        if not roi:
            try:
                image0 = context.tasker.controller.post_screencap().wait().get()
                h, w = image0.shape[:2]
            except Exception as e:
                logger.warning(f"[jingjichang] 初始截图失败（{e}），本轮跳过。")
                return CustomRecognition.AnalyzeResult(box=None, detail="初始截图异常")
            ratio = param.get("jjc_roi_ratio", DEFAULT_ROI_RATIO)
            roi = self._default_roi(h, w, ratio)
        logger.info(f"[jingjichang] 蹲开始匹配按钮，ROI={roi}，最多等待{max_wait}s。")

        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                image = context.tasker.controller.post_screencap().wait().get()
                if image is None:
                    raise RuntimeError("post_screencap 返回 None")
            except Exception as e:
                logger.warning(f"[jingjichang] 截图失败（{e}），重试中...")
                time.sleep(interval)
                continue

            try:
                reco = context.run_recognition(
                    self._ROC_NAME,
                    image,
                    pipeline_override={
                        self._ROC_NAME: {
                            "recognition": "OCR",
                            "roi": roi,
                            "expected": expected,
                            "threshold": 0.6,
                        }
                    },
                )
            except Exception as e:
                logger.warning(f"[jingjichang] OCR 调用异常（{e}），重试中...")
                time.sleep(interval)
                continue

            if reco and reco.hit and reco.box:
                logger.info("[jingjichang] ✅ 识别到开始匹配按钮，返回命中框。")
                if notify and time.time() - self._LAST_NOTIFY_TS >= 300:
                    try:
                        if send_message("竞技场挂机", "已找到开始匹配按钮，准备点击。"):
                            self._LAST_NOTIFY_TS = time.time()
                    except Exception as e:
                        logger.warning(f"[jingjichang] 通知发送异常（{e}）。")
                return CustomRecognition.AnalyzeResult(
                    box=reco.box, detail="识别到开始匹配按钮"
                )
            time.sleep(interval)

        logger.info("[jingjichang] 本轮等待超时，未出现开始匹配按钮。")
        return CustomRecognition.AnalyzeResult(box=None, detail="等待超时，未出现开始匹配")
