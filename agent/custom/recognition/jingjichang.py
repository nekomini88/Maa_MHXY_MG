# -*- coding: utf-8 -*-
"""竞技场挂机识别 + 点击（CustomRecognition 供 pipeline 调用）。

自定义识别器 ``jingjichang``：监测「竞技场」面板上的「开始匹配」**按钮**，
命中后在本识别器内直接点击（点击次数即挂机场次），点满 ``jjc_max_clicks``
次返回成功、由 pipeline 收尾。

与擂台/剑会同一套识别逻辑：**锚定关键词 + 全屏扫描 + 候选框复查**。
公共部件在 ``agent/utils/ocr_button.py``。

为什么不沿用「包含匹配」+ 固定比例 ROI
--------------------------------------
1. ``expected`` 是子串正则（``boost::regex_search``）：面板上的说明文字
   「开始匹配后自动进入对局」同样命中，而 ``cherry_pick()`` 默认取**最靠左**
   的框 —— 说明排在按钮左边时，点下去的是文字而不是按钮；
2. 固定比例 ROI ``[0.76w, 0.75h, 0.18w, 0.23h]`` 是按一张横屏截图量的，
   换分辨率/竖屏会整块错开，把按钮排除在扫描区外。

挂机语义：识别器内循环「等按钮 → 点击 → 等 ``jjc_click_delay`` 秒」，
``jjc_max_clicks`` 次后返回成功（pipeline 里 ``next`` 回主界面判断，任务收尾）。
把 ``jjc_max_clicks`` 设为 ``0`` 或负数即**不限次数/常驻**：一直挂到手动
「停止任务」为止，停止请求在一个轮询点内生效。

配置（pipeline 节点 custom_recognition_param 传入）：
    jjc_enabled:      是否启用（默认 true）
    jjc_expected:     OCR 关键词（默认 ["^\\\\s*开始匹配\\\\s*$"]，锚定正则）
    jjc_roi:          自定义 ROI [x,y,w,h] 绝对像素（默认全屏 [0,0,0,0]）
    jjc_roi_ratio:    按比例算 ROI [x,y,w,h]，显式给出才生效
    jjc_threshold:    OCR 置信度阈值（默认 0.6）
    jjc_max_wait:     单次等按钮最长秒数（默认 600，够打完一场的时间）
    jjc_interval:     轮询截图间隔秒（默认 2.0，挂机场景不必太密）
    jjc_max_clicks:   打满多少次结束（默认 10；<=0 表示不限次数、一直挂）
    jjc_click_delay:  每次点击后等待秒数（默认 5，等界面切走再继续蹲）
    jjc_notify:       命中是否发通知（默认 false，挂机不打扰）
"""

import json
import time

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition
from maa.context import Context

from utils import logger
from utils import send_message
from utils.ocr_button import (
    ANCHORED_EXACT,
    pick_button_box,
    ratio_roi,
    tasker_stopping,
)

DEFAULT_EXPECTED = [ANCHORED_EXACT]
DEFAULT_ROI = [0, 0, 0, 0]  # 全屏：不依赖分辨率与横竖屏
DEFAULT_THRESHOLD = 0.6
DEFAULT_MAX_WAIT = 600
DEFAULT_INTERVAL = 2.0
DEFAULT_MAX_CLICKS = 10
DEFAULT_CLICK_DELAY = 5.0


@AgentServer.custom_recognition("jingjichang")
class Jingjichang(CustomRecognition):
    """识别「开始匹配」按钮并点击，点满设定次数即收工（循环挂机）。"""

    _ROC_NAME = "jingjichang-ocr"
    _LAST_NOTIFY_TS = 0.0
    _CLICK_COUNT = 0  # 本轮挂机已点击次数（满 jjc_max_clicks 即结束）

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
        threshold = float(param.get("jjc_threshold", DEFAULT_THRESHOLD))
        max_wait = float(param.get("jjc_max_wait", DEFAULT_MAX_WAIT))
        interval = float(param.get("jjc_interval", DEFAULT_INTERVAL))
        max_clicks = int(param.get("jjc_max_clicks", DEFAULT_MAX_CLICKS))
        click_delay = float(param.get("jjc_click_delay", DEFAULT_CLICK_DELAY))
        notify = bool(param.get("jjc_notify", False))
        roi = param.get("jjc_roi") or DEFAULT_ROI

        # 计数必须落在类上：analyze 每次都可能是新实例，写在实例上会让
        # 「已打满几次」在 pipeline 的 on_error 重试后就归零，越点越多。
        cls = type(self)

        ratio = param.get("jjc_roi_ratio")
        need_capture_for_roi = (
            isinstance(ratio, (list, tuple))
            and len(ratio) == 4
            and not param.get("jjc_roi")
        )

        unlimited = max_clicks <= 0
        goal = "不限次数" if unlimited else f"{max_clicks}次"
        logger.info(
            f"[jingjichang] 蹲「开始匹配」按钮，ROI={roi}，目标{goal}，"
            f"已点{cls._CLICK_COUNT}次。"
        )

        last_box = None
        while unlimited or cls._CLICK_COUNT < max_clicks:
            if tasker_stopping(context):
                logger.info(
                    f"[jingjichang] 收到停止请求，结束挂机（已点{cls._CLICK_COUNT}次）。"
                )
                return CustomRecognition.AnalyzeResult(box=None, detail="任务已请求停止")

            box, why, failed = self._wait_button(
                context, roi, expected, threshold, max_wait, interval, ratio if need_capture_for_roi else None
            )
            if failed:
                # 停止请求或连续截图/OCR 异常：不算点击，交回 pipeline 决定。
                return CustomRecognition.AnalyzeResult(box=None, detail=why)
            if not box:
                logger.info(
                    f"[jingjichang] 等待超时（已点{cls._CLICK_COUNT}次），"
                    "返回失败由 pipeline 重试，计数保留。"
                )
                return CustomRecognition.AnalyzeResult(
                    box=None, detail=f"等待超时，已点{cls._CLICK_COUNT}次"
                )

            x, y, w, h = box
            try:
                context.tasker.controller.post_click(x + w // 2, y + h // 2).wait()
            except Exception as e:
                logger.warning(f"[jingjichang] 点击失败（{e}），下轮重试。")
                time.sleep(interval)
                continue

            cls._CLICK_COUNT += 1
            last_box = box
            logger.info(
                f"[jingjichang] ✅ 第{cls._CLICK_COUNT}次点击开始匹配 "
                f"（框={box}，{why}）。"
            )
            if notify:
                try:
                    done_text = f"{cls._CLICK_COUNT}/{max_clicks}" if not unlimited else f"{cls._CLICK_COUNT}"
                    send_message("竞技场挂机", f"已点击开始匹配（{done_text}）。")
                except Exception as e:
                    logger.warning(f"[jingjichang] 通知发送异常（{e}）。")
            time.sleep(click_delay)

        done = cls._CLICK_COUNT
        cls._CLICK_COUNT = 0  # 复位，任务重跑从 0 开始
        logger.info(f"[jingjichang] 🎉 已打满{done}次，任务完成。")
        return CustomRecognition.AnalyzeResult(
            box=last_box, detail=f"竞技场挂机完成，已打满{done}次"
        )

    def _wait_button(
        self,
        context,
        roi,
        expected,
        threshold,
        max_wait,
        interval,
        ratio=None,
    ):
        """等按钮出现，返回 ``(box, 说明, 是否异常退出)``；超时返回 ``(None, ...)``。

        只会返回**整段文本等于「开始匹配」**的框：说明行、日志行含关键词也
        不会被当按钮点掉（见 ``utils.ocr_button``）。
        """
        deadline = time.time() + max_wait
        while time.time() < deadline:
            if tasker_stopping(context):
                return None, "任务已请求停止", True

            try:
                image = context.tasker.controller.post_screencap().wait().get()
                if image is None:
                    raise RuntimeError("post_screencap 返回 None")
            except Exception as e:
                logger.warning(f"[jingjichang] 截图失败（{e}），重试中...")
                time.sleep(interval)
                continue

            if ratio is not None:
                roi = ratio_roi(image, ratio)

            try:
                reco = context.run_recognition(
                    self._ROC_NAME,
                    image,
                    pipeline_override={
                        self._ROC_NAME: {
                            "recognition": "OCR",
                            "roi": roi,
                            "expected": expected,
                            "threshold": threshold,
                        }
                    },
                )
            except Exception as e:
                logger.warning(f"[jingjichang] OCR 调用异常（{e}），重试中...")
                time.sleep(interval)
                continue

            box, why = pick_button_box(reco) if reco and reco.hit else (None, "未命中")
            if box:
                return box, why, False
            time.sleep(interval)
        return None, "等待超时", False
