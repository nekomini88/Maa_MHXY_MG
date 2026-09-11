# -*- coding: utf-8 -*-
"""擂台匹配按钮识别 + 点击（CustomRecognition 供 pipeline 调用）。

自定义识别器 ``leitai_pipei``：监测「擂台大战」面板上的「开始匹配」**按钮**，
出现即返回按钮命中框，由 pipeline 节点执行 Click 点下。

与剑会同一套逻辑：**锚定关键词 + 全屏扫描 + 候选框复查**，单轮等不到按钮就
交给 pipeline 的 ``on_error`` 自循环继续守，任务不结束。公共部件在
``agent/utils/ocr_button.py``（三个「自动点开始匹配」任务共用，改一处三处生效）。

为什么不沿用「包含匹配」+ 固定比例 ROI
--------------------------------------
1. ``expected`` 走的是子串正则（``boost::regex_search``）：面板上的提示文字
   「点击开始匹配，队伍满5人开战」同样命中，而 ``cherry_pick()`` 默认取
   **最靠左**的框 —— 提示排在按钮左边时，Click 就点在提示文字上；
2. 固定比例 ROI ``[0.48w, 0.70h, 0.32w, 0.25h]`` 是按一张横屏截图量的，
   换分辨率/竖屏就会整块错开，反而把按钮排除在扫描区外；
3. 左下的「队伍组满5人方可开始擂台乱斗匹配」提示含「匹配」二字，靠叠加
   关键词和 ROI 隔离来防误报本来就脆 —— 锚定「整段文本等于 开始匹配」后
   这类文本结构上不可能命中。

常驻语义：本识别器**不负责结束任务**。单轮最多守候 ``leitai_max_wait`` 秒，
等不到就返回未命中，由 pipeline 节点的 ``on_error`` 自循环回本节点再等；
命中并点击后由 ``next`` 自循环回本节点，等下一次弹窗（队伍重组、打完一场
回房间、匹配失败重试）。要结束任务只能手动「停止任务」，停止请求在一个
轮询点内生效。

配置（pipeline 节点 custom_recognition_param 传入）：
    leitai_enabled:          是否启用（默认 true）
    leitai_expected:         OCR 关键词（默认 ["^\\\\s*开始匹配\\\\s*$"]，锚定正则）
    leitai_roi:              自定义 ROI [x,y,w,h] 绝对像素（默认全屏 [0,0,0,0]）
    leitai_roi_ratio:        按比例算 ROI [x,y,w,h]，显式给出才生效
    leitai_threshold:        OCR 置信度阈值（默认 0.6）
    leitai_max_wait:         单轮 analyze 最长等待秒数（默认 300）
    leitai_interval:         轮询截图间隔秒（默认 1.0）
    leitai_click_cooldown:   两次命中之间的最小间隔秒（默认 2.0，防同一弹窗连点）
    leitai_notify:           命中是否发通知（默认 false，常规匹配不打扰）
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
DEFAULT_MAX_WAIT = 300
DEFAULT_INTERVAL = 1.0
DEFAULT_CLICK_COOLDOWN = 2.0


@AgentServer.custom_recognition("leitai_pipei")
class LeitaiPipei(CustomRecognition):
    """识别「开始匹配」按钮，命中返回按钮框供 pipeline 点击。"""

    _ROC_NAME = "leitai-pipei-ocr"
    _LAST_NOTIFY_TS = 0.0
    _LAST_HIT_TS = 0.0

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
        threshold = float(param.get("leitai_threshold", DEFAULT_THRESHOLD))
        max_wait = float(param.get("leitai_max_wait", DEFAULT_MAX_WAIT))
        interval = float(param.get("leitai_interval", DEFAULT_INTERVAL))
        click_cooldown = float(
            param.get("leitai_click_cooldown", DEFAULT_CLICK_COOLDOWN)
        )
        notify = bool(param.get("leitai_notify", False))
        roi = param.get("leitai_roi") or DEFAULT_ROI

        # 冷却/节流状态必须落在类上：analyze 每次都可能是新实例，
        # 写到 self 上会随实例一起丢掉（连点与重复通知都拦不住）。
        cls = type(self)

        ratio = param.get("leitai_roi_ratio")
        need_capture_for_roi = (
            isinstance(ratio, (list, tuple))
            and len(ratio) == 4
            and not param.get("leitai_roi")
        )

        logger.info(
            f"[leitai_pipei] 开始守候「开始匹配」按钮，ROI={roi}，"
            f"关键词={expected}，最多等待{max_wait}s。"
        )

        deadline = time.time() + max_wait
        while time.time() < deadline:
            if tasker_stopping(context):
                logger.info("[leitai_pipei] 收到停止请求，立即结束本轮守候。")
                return CustomRecognition.AnalyzeResult(box=None, detail="任务已请求停止")

            try:
                image = context.tasker.controller.post_screencap().wait().get()
                if image is None:
                    raise RuntimeError("post_screencap 返回 None")
            except Exception as e:
                logger.warning(f"[leitai_pipei] 截图失败（{e}），重试中...")
                time.sleep(interval)
                continue

            if need_capture_for_roi:
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
                logger.warning(f"[leitai_pipei] OCR 调用异常（{e}），重试中...")
                time.sleep(interval)
                continue

            box, why = pick_button_box(reco) if reco and reco.hit else (None, "未命中")
            if box:
                gap = time.time() - cls._LAST_HIT_TS
                if 0 <= gap < click_cooldown:
                    time.sleep(click_cooldown - gap)
                    if tasker_stopping(context):
                        logger.info("[leitai_pipei] 冷却期间收到停止请求，结束本轮守候。")
                        return CustomRecognition.AnalyzeResult(
                            box=None, detail="任务已请求停止"
                        )
                cls._LAST_HIT_TS = time.time()
                cx, cy = box[0] + box[2] // 2, box[1] + box[3] // 2
                logger.info(
                    f"[leitai_pipei] ✅ 命中「开始匹配」按钮 {why}，"
                    f"框={box}（中心=({cx},{cy})，框架在框内取随机点），交给 pipeline Click。"
                )
                if notify and time.time() - cls._LAST_NOTIFY_TS >= 300:
                    try:
                        if send_message("擂台匹配", "已找到开始匹配按钮，准备点击。"):
                            cls._LAST_NOTIFY_TS = time.time()
                    except Exception as e:
                        logger.warning(f"[leitai_pipei] 通知发送异常（{e}）。")
                return CustomRecognition.AnalyzeResult(box=box, detail=f"命中按钮：{why}")

            time.sleep(interval)

        logger.info("[leitai_pipei] 本轮等待超时，未出现开始匹配按钮，继续守候。")
        return CustomRecognition.AnalyzeResult(
            box=None, detail="本轮等待超时，未出现开始匹配按钮，继续守候"
        )
