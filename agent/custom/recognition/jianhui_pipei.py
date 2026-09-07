# -*- coding: utf-8 -*-
"""剑会匹配按钮识别 + 点击（CustomRecognition 供 pipeline 调用）。

自定义识别器 ``jianhui_pipei``：监测剑会房间弹窗内右侧的「开始匹配」
按钮，出现即返回命中框，由 pipeline 节点执行 Click 点下。

按钮位置（横屏模拟器，比例定位兼容不同分辨率）：
  实测 1080x607 截图：按钮约 x 545~695，y 350~405。
  默认 ROI 取相对比例 [0.27w, 0.18h, 0.48w, 0.50h]，覆盖中央弹窗区、
  同时用「开始匹配」全词匹配，避免误点左侧「我再等等」按钮
  （弹窗文字「请及时开始匹配」含关键词但不在按钮上，OCR 返回框以按钮为准）。

配置（pipeline 节点 custom_recognition_param 传入）：
    jianhui_enabled:          是否启用（默认 true）
    jianhui_roi:              自定义 ROI [x,y,w,h] 绝对像素（默认按比例计算）
    jianhui_roi_ratio:        默认 ROI 比例 [x,y,w,h]（默认 [0.27,0.18,0.48,0.50]）
    jianhui_expected:         匹配词（默认 ["开始匹配","开始匹","始匹配"]）
    jianhui_max_wait:         单轮 analyze 最长等待秒数（默认 300）
    jianhui_interval:         轮询截图间隔秒（默认 1.0）
    jianhui_notify:           命中是否发通知（默认 false，常规匹配不打扰）
"""

import json
import time

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition
from maa.context import Context

from utils import logger
from utils import send_message

DEFAULT_ROI_RATIO = [0.27, 0.18, 0.48, 0.50]
DEFAULT_EXPECTED = ["开始匹配", "开始匹", "始匹配"]
DEFAULT_MAX_WAIT = 300
DEFAULT_INTERVAL = 1.0


@AgentServer.custom_recognition("jianhui_pipei")
class JianhuiPipei(CustomRecognition):
    """识别「开始匹配」按钮，命中返回框供 pipeline 点击。"""

    _ROC_NAME = "jianhui-pipei-ocr"
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

        enabled = param.get("jianhui_enabled", True)
        if not enabled:
            return CustomRecognition.AnalyzeResult(box=None, detail="剑会匹配已禁用")

        expected = param.get("jianhui_expected", DEFAULT_EXPECTED)
        max_wait = float(param.get("jianhui_max_wait", DEFAULT_MAX_WAIT))
        interval = float(param.get("jianhui_interval", DEFAULT_INTERVAL))
        notify = bool(param.get("jianhui_notify", False))

        roi = param.get("jianhui_roi")
        if not roi:
            try:
                image0 = context.tasker.controller.post_screencap().wait().get()
                h, w = image0.shape[:2]
            except Exception as e:
                logger.warning(f"[jianhui_pipei] 初始截图失败（{e}），本轮跳过。")
                return CustomRecognition.AnalyzeResult(box=None, detail="初始截图异常")
            ratio = param.get("jianhui_roi_ratio", DEFAULT_ROI_RATIO)
            roi = self._default_roi(h, w, ratio)
        logger.info(f"[jianhui_pipei] 开始等待开始匹配按钮，ROI={roi}，最多等待{max_wait}s。")

        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                image = context.tasker.controller.post_screencap().wait().get()
                if image is None:
                    raise RuntimeError("post_screencap 返回 None")
            except Exception as e:
                logger.warning(f"[jianhui_pipei] 截图失败（{e}），重试中...")
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
                logger.warning(f"[jianhui_pipei] OCR 调用异常（{e}），重试中...")
                time.sleep(interval)
                continue

            if reco and reco.hit and reco.box:
                logger.info("[jianhui_pipei] ✅ 识别到开始匹配按钮，返回命中框。")
                if notify and time.time() - self._LAST_NOTIFY_TS >= 300:
                    try:
                        if send_message("剑会匹配", "已找到开始匹配按钮，准备点击。"):
                            self._LAST_NOTIFY_TS = time.time()
                    except Exception as e:
                        logger.warning(f"[jianhui_pipei] 通知发送异常（{e}）。")
                return CustomRecognition.AnalyzeResult(
                    box=reco.box, detail="识别到开始匹配按钮"
                )
            time.sleep(interval)

        logger.info("[jianhui_pipei] 本轮等待超时，未出现开始匹配按钮。")
        return CustomRecognition.AnalyzeResult(box=None, detail="等待超时，未出现开始匹配")
