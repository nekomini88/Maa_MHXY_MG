# -*- coding: utf-8 -*-
"""剑会匹配按钮识别 + 点击（CustomRecognition 供 pipeline 调用）。

自定义识别器 ``jianhui_pipei``：监测剑会房间弹窗内右侧的「开始匹配」
按钮，出现即返回命中框，由 pipeline 节点执行 Click 点下。

按钮位置（横屏模拟器，比例定位兼容不同分辨率）：
  实测 1080x607 截图：按钮约 x 545~695，y 350~405。
  默认 ROI 取相对比例 [0.27w, 0.18h, 0.48w, 0.50h]，覆盖中央弹窗区、
  同时用「开始匹配」全词匹配，避免误点左侧「我再等等」按钮
  （弹窗文字「请及时开始匹配」含关键词但不在按钮上，OCR 返回框以按钮为准）。

常驻语义（关键设计）：本识别器**不负责结束任务**。单轮最多守候
``jianhui_max_wait`` 秒，等不到按钮就返回未命中，由 pipeline 节点的
``on_error`` 自循环回到本节点继续等；命中并点击后同样由 ``next`` 自循环
回到本节点，等下一次弹窗。所以「点一次就跑完退出」在这里是故障而不是
期望行为——剑会弹窗会反复出现（队友取消、匹配失败重试、打完一局回到
房间），必须一直守着。任务的结束只由用户手动停止触发，停止请求会在
下个轮询点立刻生效。

配置（pipeline 节点 custom_recognition_param 传入）：
    jianhui_enabled:          是否启用（默认 true）
    jianhui_roi:              自定义 ROI [x,y,w,h] 绝对像素（默认按比例计算）
    jianhui_roi_ratio:        默认 ROI 比例 [x,y,w,h]（默认 [0.27,0.18,0.48,0.50]）
    jianhui_expected:         匹配词（默认 ["开始匹配","开始匹","始匹配"]）
    jianhui_max_wait:         单轮 analyze 最长等待秒数（默认 300）
    jianhui_interval:         轮询截图间隔秒（默认 1.0）
    jianhui_click_cooldown:   两次命中之间的最小间隔秒（默认 2.0，防同一弹窗连点）
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
DEFAULT_CLICK_COOLDOWN = 2.0


def _tasker_stopping(context: Context) -> bool:
    """任务是否已被请求停止。

    只看 ``tasker.stopping``：``tasker.running`` 在部分版本里会被识别回调
    自身改写，用它判断会让正常轮询误以为应该收工。
    """
    return bool(getattr(getattr(context, "tasker", None), "stopping", False))


@AgentServer.custom_recognition("jianhui_pipei")
class JianhuiPipei(CustomRecognition):
    """识别「开始匹配」按钮，命中返回框供 pipeline 点击。"""

    _ROC_NAME = "jianhui-pipei-ocr"
    _LAST_NOTIFY_TS = 0.0
    _LAST_HIT_TS = 0.0

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
        click_cooldown = float(
            param.get("jianhui_click_cooldown", DEFAULT_CLICK_COOLDOWN)
        )
        notify = bool(param.get("jianhui_notify", False))

        # 冷却/节流状态必须落在类上：analyze 每次都可能是新实例，
        # 写到 self 上会随实例一起丢掉（连点与重复通知都拦不住）。
        cls = type(self)

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
            if _tasker_stopping(context):
                logger.info("[jianhui_pipei] 收到停止请求，立即结束本轮守候。")
                return CustomRecognition.AnalyzeResult(box=None, detail="任务已请求停止")

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
                # 同一弹窗可能因动画/网络延迟在屏幕上多停留一会儿，
                # 命中后按冷却间隔节流，避免对着同一个按钮连点。
                gap = time.time() - cls._LAST_HIT_TS
                if 0 <= gap < click_cooldown:
                    time.sleep(click_cooldown - gap)
                    if _tasker_stopping(context):
                        logger.info("[jianhui_pipei] 冷却期间收到停止请求，结束本轮守候。")
                        return CustomRecognition.AnalyzeResult(
                            box=None, detail="任务已请求停止"
                        )
                cls._LAST_HIT_TS = time.time()
                logger.info("[jianhui_pipei] ✅ 识别到开始匹配按钮，返回命中框。")
                if notify and time.time() - cls._LAST_NOTIFY_TS >= 300:
                    try:
                        if send_message("剑会匹配", "已找到开始匹配按钮，准备点击。"):
                            cls._LAST_NOTIFY_TS = time.time()
                    except Exception as e:
                        logger.warning(f"[jianhui_pipei] 通知发送异常（{e}）。")
                return CustomRecognition.AnalyzeResult(
                    box=reco.box, detail="识别到开始匹配按钮"
                )
            time.sleep(interval)

        logger.info("[jianhui_pipei] 本轮等待超时，未出现开始匹配按钮，继续守候。")
        return CustomRecognition.AnalyzeResult(
            box=None, detail="本轮等待超时，未出现开始匹配按钮，继续守候"
        )
