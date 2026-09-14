# -*- coding: utf-8 -*-
"""竞技场「开始匹配」按钮识别（CustomRecognition 供 pipeline 调用）。

自定义识别器 ``jingjichang``：监测「竞技场」面板上的「开始匹配」**按钮**，
出现即返回按钮命中框，由 pipeline 节点执行 Click 点下 —— 与擂台
（``leitai_pipei``）、剑会（``jianhui_pipei``）同一套写法，三个任务的
公共部件都在 ``agent/utils/ocr_button.py``（改一处三个都受益）。

为什么不沿用「包含匹配」+ 固定比例 ROI
--------------------------------------
1. ``expected`` 是子串正则（``boost::regex_search``）：面板上的说明文字
   「开始匹配后自动进入对局」同样命中，而 ``cherry_pick()`` 默认取**最靠左**
   的框 —— 说明排在按钮左边时，点下去的是文字而不是按钮；
2. 固定比例 ROI ``[0.76w, 0.75h, 0.18w, 0.23h]`` 是按一张横屏截图量的，
   换分辨率/竖屏会整块错开，把按钮排除在扫描区外；
3. 竞技场还有一个自己的坑：按钮文字是**上下两行**，OCR 会把「开始」和
   「匹配」各读成一个框（真机 918x500 实测）。此时整段关键词的正则一个都
   不命中，现象正是「按钮明明在屏幕上，任务却一直在蹲、从没点过」——
   锚定要配 :func:`expected_patterns` 的半行入口 + ``pick_button_box`` 的
   两行合框，缺一不可。

常驻语义：本识别器**不负责结束任务**，也不自己点。单轮最多守候
``jjc_max_wait`` 秒，等不到就返回未命中，由 pipeline 节点的 ``on_error``
自循环回本节点再等；命中后由 ``next`` 自循环回本节点等下一次
（打完一局回面板、匹配失败重试、队伍重组）。要结束任务只能手动
「停止任务」，停止请求在一个轮询点内生效。

配置（pipeline 节点 custom_recognition_param 传入）：
    jjc_enabled:        是否启用（默认 true）
    jjc_expected:       OCR 关键词（默认整段锚定 + 拆行半行锚定，见 expected_patterns）
    jjc_roi:            自定义 ROI [x,y,w,h] 绝对像素（默认全屏 [0,0,0,0]）
    jjc_roi_ratio:      按比例算 ROI [x,y,w,h]，显式给出才生效
    jjc_threshold:      OCR 置信度阈值（默认 0.6）
    jjc_max_wait:       单次等按钮最长秒数（默认 600，够打完一场）
    jjc_interval:       轮询截图间隔秒（默认 2.0，挂机场景不必太密）
    jjc_click_cooldown: 两次命中之间的最小间隔秒（默认 5.0，防同一面板连点）
    jjc_notify:         命中是否发通知（默认 false，挂机不打扰）
"""

import json
import time

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition
from maa.context import Context

from utils import logger
from utils import send_message
from utils.ocr_button import (
    expected_patterns,
    pick_button_box,
    ratio_roi,
    tasker_stopping,
)

DEFAULT_EXPECTED = expected_patterns()
DEFAULT_ROI = [0, 0, 0, 0]  # 全屏：不依赖分辨率与横竖屏
DEFAULT_THRESHOLD = 0.6
DEFAULT_MAX_WAIT = 600
DEFAULT_INTERVAL = 2.0
DEFAULT_CLICK_COOLDOWN = 5.0


@AgentServer.custom_recognition("jingjichang")
class Jingjichang(CustomRecognition):
    """识别「开始匹配」按钮，命中返回按钮框供 pipeline 点击。"""

    _ROC_NAME = "jingjichang-ocr"
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

        enabled = param.get("jjc_enabled", True)
        if not enabled:
            return CustomRecognition.AnalyzeResult(box=None, detail="竞技场挂机已禁用")

        expected = param.get("jjc_expected", DEFAULT_EXPECTED)
        threshold = float(param.get("jjc_threshold", DEFAULT_THRESHOLD))
        max_wait = float(param.get("jjc_max_wait", DEFAULT_MAX_WAIT))
        interval = float(param.get("jjc_interval", DEFAULT_INTERVAL))
        click_cooldown = float(
            param.get("jjc_click_cooldown", DEFAULT_CLICK_COOLDOWN)
        )
        notify = bool(param.get("jjc_notify", False))
        roi = param.get("jjc_roi") or DEFAULT_ROI

        # 冷却/节流状态必须落在类上：analyze 每次都可能是新实例，
        # 写到 self 上会随实例一起丢掉（连点与重复通知都拦不住）。
        cls = type(self)

        ratio = param.get("jjc_roi_ratio")
        need_capture_for_roi = (
            isinstance(ratio, (list, tuple))
            and len(ratio) == 4
            and not param.get("jjc_roi")
        )

        logger.info(
            f"[jingjichang] 开始守候竞技场「开始匹配」按钮，ROI={roi}，"
            f"关键词={expected}，最多等待{max_wait}s。"
        )

        deadline = time.time() + max_wait
        while time.time() < deadline:
            if tasker_stopping(context):
                logger.info("[jingjichang] 收到停止请求，立即结束本轮守候。")
                return CustomRecognition.AnalyzeResult(box=None, detail="任务已请求停止")

            try:
                image = context.tasker.controller.post_screencap().wait().get()
                if image is None:
                    raise RuntimeError("post_screencap 返回 None")
            except Exception as e:
                logger.warning(f"[jingjichang] 截图失败（{e}），重试中...")
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
                logger.warning(f"[jingjichang] OCR 调用异常（{e}），重试中...")
                time.sleep(interval)
                continue

            box, why = pick_button_box(reco) if reco and reco.hit else (None, "未命中")
            if box:
                gap = time.time() - cls._LAST_HIT_TS
                if 0 <= gap < click_cooldown:
                    time.sleep(click_cooldown - gap)
                    if tasker_stopping(context):
                        logger.info("[jingjichang] 冷却期间收到停止请求，结束本轮守候。")
                        return CustomRecognition.AnalyzeResult(
                            box=None, detail="任务已请求停止"
                        )
                cls._LAST_HIT_TS = time.time()
                cx, cy = box[0] + box[2] // 2, box[1] + box[3] // 2
                logger.info(
                    f"[jingjichang] ✅ 命中「开始匹配」按钮 {why}，"
                    f"框={box}（中心=({cx},{cy})，框架在框内取随机点），交给 pipeline Click。"
                )
                if notify and time.time() - cls._LAST_NOTIFY_TS >= 300:
                    try:
                        if send_message("竞技场", "已找到开始匹配按钮，准备点击。"):
                            cls._LAST_NOTIFY_TS = time.time()
                    except Exception as e:
                        logger.warning(f"[jingjichang] 通知发送异常（{e}）。")
                return CustomRecognition.AnalyzeResult(box=box, detail=f"命中按钮：{why}")

            time.sleep(interval)

        logger.info("[jingjichang] 本轮等待超时，未出现开始匹配按钮，继续守候。")
        return CustomRecognition.AnalyzeResult(
            box=None, detail="本轮等待超时，未出现开始匹配按钮，继续守候"
        )
