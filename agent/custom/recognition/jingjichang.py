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
    jjc_max_clicks:        打满多少次结束（默认 10，点击在 analyze 内直接执行，
                           满次后返回成功，pipeline 即结束任务）
    jjc_click_delay:       每次点击后等待秒数（默认 5，等界面切走再继续蹲）
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
    _CLICK_COUNT = 0  # 本轮挂机已点击次数（满 jjc_max_clicks 即结束）

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
        max_clicks = int(param.get("jjc_max_clicks", 10))
        click_delay = float(param.get("jjc_click_delay", 5))

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
        logger.info(f"[jingjichang] 蹲开始匹配按钮，ROI={roi}，目标{max_clicks}次，已点{self._CLICK_COUNT}次。")

        while self._CLICK_COUNT < max_clicks:
            box = self._wait_button(context, roi, expected, max_wait, interval)
            if not box:
                logger.info(
                    f"[jingjichang] 等待超时（已点{self._CLICK_COUNT}/{max_clicks}次），"
                    "返回失败由 pipeline 重试，计数保留。"
                )
                return CustomRecognition.AnalyzeResult(
                    box=None, detail=f"等待超时，已点{self._CLICK_COUNT}/{max_clicks}次"
                )
            x, y, w, h = box
            try:
                context.tasker.controller.post_click(x + w // 2, y + h // 2).wait()
                self._CLICK_COUNT += 1
                logger.info(
                    f"[jingjichang] ✅ 第{self._CLICK_COUNT}/{max_clicks}次点击开始匹配。"
                )
            except Exception as e:
                logger.warning(f"[jingjichang] 点击失败（{e}），下轮重试。")
            if notify:
                try:
                    send_message("竞技场挂机", f"已点击开始匹配（{self._CLICK_COUNT}/{max_clicks}）。")
                except Exception as e:
                    logger.warning(f"[jingjichang] 通知发送异常（{e}）。")
            time.sleep(click_delay)

        done = self._CLICK_COUNT
        self._CLICK_COUNT = 0  # 复位，任务重跑从 0 开始
        logger.info(f"[jingjichang] 🎉 已打满{done}次，任务完成。")
        return CustomRecognition.AnalyzeResult(
            box=roi, detail=f"竞技场挂机完成，已打满{done}次"
        )

    def _wait_button(self, context, roi, expected, max_wait, interval):
        """等待按钮出现，返回命中框；超时返回 None。"""
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
                return reco.box
            time.sleep(interval)
        return None
