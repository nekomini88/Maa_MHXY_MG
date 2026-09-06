# -*- coding: utf-8 -*-
"""擂台匹配按钮识别 + 点击（CustomRecognition 供 pipeline 调用）。

自定义识别器 ``leitai_pipei``：监测「擂台大战」面板右下角的「开始匹配」
按钮，出现即返回命中框，由 pipeline 节点执行 Click 点下。

按钮位置（横屏模拟器，比例定位兼容不同分辨率）：
  实测 940x542 截图：按钮约 x 500~685，y 435~490。
  默认 ROI 取相对比例 [0.48w, 0.70h, 0.32w, 0.25h]，覆盖按钮区、
  同时避开左下方「队伍组满5人方可开始擂台乱斗匹配」提示文字
  （该提示含「匹配」二字，必须用「开始匹配」全词匹配 + ROI 隔离防误报）。

配置（pipeline 节点 custom_recognition_param 传入）：
    leitai_enabled:          是否启用（默认 true）
    leitai_roi:              自定义 ROI [x,y,w,h] 绝对像素（默认按比例计算）
    leitai_roi_ratio:        默认 ROI 比例 [x,y,w,h]（默认 [0.48,0.70,0.32,0.25]）
    leitai_expected:         匹配词（默认 ["开始匹配","开始匹","始匹配"]）
    leitai_max_wait:         单轮 analyze 最长等待秒数（默认 300）
    leitai_interval:         轮询截图间隔秒（默认 1.0）
    leitai_notify:           命中是否发通知（默认 false，常规匹配不打扰）
"""

import json
import time

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition
from maa.context import Context

from utils import logger
from utils import send_message

DEFAULT_ROI_RATIO = [0.48, 0.70, 0.32, 0.25]
DEFAULT_EXPECTED = ["开始匹配", "开始匹", "始匹配"]
DEFAULT_MAX_WAIT = 300
DEFAULT_INTERVAL = 1.0


@AgentServer.custom_recognition("leitai_pipei")
class LeitaiPipei(CustomRecognition):
    """识别「开始匹配」按钮，命中返回框供 pipeline 点击。"""

    _ROC_NAME = "leitai-pipei-ocr"
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

        enabled = param.get("leitai_enabled", True)
        if not enabled:
            return CustomRecognition.AnalyzeResult(box=None, detail="擂台匹配已禁用")

        expected = param.get("leitai_expected", DEFAULT_EXPECTED)
        max_wait = float(param.get("leitai_max_wait", DEFAULT_MAX_WAIT))
        interval = float(param.get("leitai_interval", DEFAULT_INTERVAL))
        notify = bool(param.get("leitai_notify", False))

        roi = param.get("leitai_roi")
        if not roi:
            try:
                image0 = context.tasker.controller.post_screencap().wait().get()
                h, w = image0.shape[:2]
            except Exception as e:
                logger.warning(f"[leitai_pipei] 初始截图失败（{e}），本轮跳过。")
                return CustomRecognition.AnalyzeResult(box=None, detail="初始截图异常")
            ratio = param.get("leitai_roi_ratio", DEFAULT_ROI_RATIO)
            roi = self._default_roi(h, w, ratio)
        logger.info(f"[leitai_pipei] 开始等待开始匹配按钮，ROI={roi}，最多等待{max_wait}s。")

        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                image = context.tasker.controller.post_screencap().wait().get()
                if image is None:
                    raise RuntimeError("post_screencap 返回 None")
            except Exception as e:
                logger.warning(f"[leitai_pipei] 截图失败（{e}），重试中...")
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
                logger.warning(f"[leitai_pipei] OCR 调用异常（{e}），重试中...")
                time.sleep(interval)
                continue

            if reco and reco.hit and reco.box:
                logger.info("[leitai_pipei] ✅ 识别到开始匹配按钮，返回命中框。")
                if notify and time.time() - self._LAST_NOTIFY_TS >= 300:
                    try:
                        if send_message("擂台匹配", "已找到开始匹配按钮，准备点击。"):
                            self._LAST_NOTIFY_TS = time.time()
                    except Exception as e:
                        logger.warning(f"[leitai_pipei] 通知发送异常（{e}）。")
                return CustomRecognition.AnalyzeResult(
                    box=reco.box, detail="识别到开始匹配按钮"
                )
            time.sleep(interval)

        logger.info("[leitai_pipei] 本轮等待超时，未出现开始匹配按钮。")
        return CustomRecognition.AnalyzeResult(box=None, detail="等待超时，未出现开始匹配")
