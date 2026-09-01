# -*- coding: utf-8 -*-
"""妖王出现识别 + 通知。

自定义识别器 ``yaowang``：对屏幕左侧【聊天栏】区域做 OCR，检测「妖王」关键词。

实测妖王提示形态（挂机主界面截图佐证）：
  当某玩家挖宝放出妖王时，【系统频道】会连续滚动出现三条公告：
    「佟得来在挖宝时放出了远古妖王，」
    「妖魔冲到了建邺城（25级可挑战），大家快去击败妖王啊！」
 这三条连续消息出现在【主界面左侧聊天栏】的中上部区域，且持续停留较久
 （三条依次滚动需数秒，足够 OCR 捕获）。

聊天栏位置（挂机主界面，横屏模拟器）：
  频道标签列 x≈16~71，系统标签 x≈112~157，消息内容 x≈110~442。
  聊天栏整体 x≈16~450（宽占比约 42%），纵向 y≈119~屏幕底部（即约 18%~100% 高度）。
  妖王公告出现在聊天栏中上部，故 ROI 需覆盖整个聊天栏，而非只看底部。

命中后：
- 调用 ``utils.send_message`` 往 Telegram 推送「妖王出现」通知（内容为妖王公告行）；
- 冷却去重：``yaowang_cooldown_seconds``（默认 60s）内只通知一次，防三条连发刷屏；
- 返回 ``AnalyzeResult(box=命中框, detail=...)`` 以供 pipeline 后续跳转（如点击剿灭）。

配置（config/config.json）：
    yaowang_enabled:         是否启用（默认 true）
    yaowang_roi:             OCR 区域 [[x,y,w,h]]（默认覆盖左侧聊天栏，比例随截图尺寸换算）
    yaowang_chat_ratio_x:    聊天栏宽占全图宽比例（默认 0.45）
    yaowang_chat_ratio_y:    聊天栏顶部占全图高比例（默认 0.18）
    yaowang_cooldown_seconds: 两次通知最小间隔秒数（默认 60）
    yaowang_expected:        要匹配的关键词（默认含"妖王""妖魔冲到了"及容错变体）
"""

import json
import time

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition
from maa.context import Context

from utils import logger
from utils import send_message

# 聊天栏 ROI 默认参数（比例，随截图尺寸换算）
DEFAULT_CHAT_RATIO_X = 0.45   # 聊天栏宽度占全图宽
DEFAULT_CHAT_RATIO_Y = 0.18   # 聊天栏顶部占全图高（约 18% 高度起）
DEFAULT_COOLDOWN = 60         # 秒（三条连发，60s 冷却足够避免刷屏）
# 实测妖王公告文本，稳定出现的关键词（含容错变体）：
#   长公告含「远古妖王/妖王」；跨城公告含「妖魔冲到了」「击败妖王」
DEFAULT_EXPECTED = ["妖王", "妖魔冲到了", "远古妖王", "击败妖王", "妖玉", "妖互"]


@AgentServer.custom_recognition("yaowang")
class Yaowang(CustomRecognition):
    """识别屏幕左侧聊天栏的「妖王」公告并发送通知。"""

    _ROC_NAME = "yaowang-ocr"
    _LAST_NOTIFY_TS = 0.0     # 最近一次通知的时间戳（防止连发刷屏）

    @staticmethod
    def _chat_roi(image_h: int, image_w: int, rx: float, ry: float) -> list:
        """按实际截图尺寸计算聊天栏 ROI（比例定位，兼容不同模拟器分辨率）。

        覆盖左侧聊天栏：x 从 0 到 rx*宽，y 从 ry*高 到 屏底。
        """
        x1 = int(image_w * rx)
        y0 = int(image_h * ry)
        return [0, y0, x1, image_h - y0]

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
        expected: list = param.get("yaowang_expected", DEFAULT_EXPECTED)

        if not expected:
            logger.warning("[yaowang] expected 关键词为空，跳过识别。")
            return CustomRecognition.AnalyzeResult(box=None, detail="未配置关键词")

        image = context.tasker.controller.post_screencap().wait().get()

        try:
            h, w = image.shape[:2]
        except Exception:
            logger.error("[yaowang] 无法获取截图尺寸，跳过。")
            return CustomRecognition.AnalyzeResult(box=None, detail="截图异常")

        # 支持自定义 ROI（数组形式覆盖多区域）
        custom_rois = param.get("yaowang_rois")
        if custom_rois:
            rois = custom_rois
        else:
            rois = [self._chat_roi(h, w, rx, ry)]
        logger.debug(f"[yaowang] 聊天栏 ROI: {rois} (图 {w}x{h})")

        hit_box = None
        hit_word = None
        full_announce = ""

        for roi in rois:
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
            texts = []
            if reco and reco.hit and reco.all_results:
                for r in reco.all_results:
                    if r.text:
                        texts.append(r.text)
                    if r.text and any(w in r.text for w in expected):
                        hit_box = r.box
                        hit_word = r.text
            full_announce += " ".join(texts)
            if hit_box:
                break

        if not hit_box:
            return CustomRecognition.AnalyzeResult(box=None, detail="未识别到妖王")

        logger.info(f"[yaowang] 识别到妖王公告：{hit_word}")

        # 冷却去重：避免三条连续公告同时触发刷屏
        now = time.time()
        if now - self._LAST_NOTIFY_TS >= cooldown:
            self._LAST_NOTIFY_TS = now
            content = (hit_word or full_announce) or "妖王"
            send_message("妖王出现", content)

        return CustomRecognition.AnalyzeResult(box=hit_box, detail=f"识别到妖王：{hit_word}")