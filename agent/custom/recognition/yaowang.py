# -*- coding: utf-8 -*-
"""妖王出现识别 + 通知。

自定义识别器 ``yaowang``：对屏幕底部【世界聊天栏/通知栏】做 OCR，检测「妖王」关键词。

实测妖王提示形态（截图录像佐证）：当某玩家挖宝放出妖王时，世界频道会出现滚动公告，例如：
  「纯哆哆在挖宝时放出了远古妖王，妖魔冲到了长寿村（50级可挑战），大家快去击败妖王！」
这类公告出现在 **屏幕最底部（≈85%~100% 高度）** 的聊天栏，且为滚动消息，
不同妖王文本不同但恒含「妖王」关键词。模拟器分辨率会变，故 ROI 按实际截图尺寸动态计算。

命中后：
- 调用 ``utils.send_message`` 往 Telegram 推送「妖王出现」通知（内容为该条公告全文）；
- 冷却去重：``yaowang_cooldown_seconds``（默认 300s）内只通知一次，防滚动同一条刷屏；
- 返回 ``AnalyzeResult(box=命中框, detail=...)`` 以供 pipeline 后续跳转（如点击剿灭）。

配置（config/config.json）：
    yaowang_enabled:         是否启用（默认 true）
    yaowang_bottom_ratio:    底部聊天栏起始高度比例（默认 0.85，即从 85% 高度起）
    yaowang_cooldown_seconds: 两次通知最小间隔秒数（默认 300）
    yaowang_expected:        要匹配的关键词（默认 ["妖王"]，含形近/容错变体）
"""

import json
import time

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition
from maa.context import Context

from utils import logger
from utils import send_message

DEFAULT_BOTTOM_RATIO = 0.85   # 底部聊天栏起始高度（占全高比例）
DEFAULT_COOLDOWN = 300        # 秒
# 实测妖王公告文本有两种形态：
#   长句：纯哆哆在挖宝时放出了远古妖王，妖魔冲到了长寿村（50级可挑战）...击败妖王！
#   短句：妖魔冲到了建帜城（25级可挑战
# 稳定出现的关键词：「妖王」与「妖魔冲到了」；后者三现实拍全含，且几乎不误报。
# OCR 可能把「妖王」误读成近形字，故保留形近变体 + 「妖魔冲到了」强特征词兜底。
DEFAULT_EXPECTED = ["妖王", "妖魔冲到了", "妖玉", "妖互", "受王", "妖主", "要王"]


@AgentServer.custom_recognition("yaowang")
class Yaowang(CustomRecognition):
    """识别屏幕底部聊天/通知栏的「妖王」公告并发送通知。"""

    _ROC_NAME = "yaowang-ocr"
    _LAST_NOTIFY_TS = 0.0     # 最近一次通知的时间戳（防止滚动公告刷屏）

    @staticmethod
    def _bottom_roi(image_h: int, image_w: int, ratio: float) -> list:
        """按实际截图尺寸计算底部聊天栏 ROI（比例定位，兼容不同模拟器分辨率）。"""
        y0 = int(image_h * ratio)
        return [0, y0, image_w, image_h - y0]

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult:
        param: dict = json.loads(argv.custom_recognition_param or "{}")

        enabled = param.get("yaowang_enabled", True)
        bottom_ratio = float(param.get("yaowang_bottom_ratio", DEFAULT_BOTTOM_RATIO))
        cooldown = int(param.get("yaowang_cooldown_seconds", DEFAULT_COOLDOWN))
        expected: list = param.get("yaowang_expected", DEFAULT_EXPECTED)

        if not expected:
            logger.warning("[yaowang] expected 关键词为空，跳过识别。")
            return CustomRecognition.AnalyzeResult(box=None, detail="未配置关键词")

        image = context.tasker.controller.post_screencap().wait().get()

        # 从截图获取实际尺寸（MaaFramework image 为 ndarray: h,w,(c)）
        try:
            h, w = image.shape[:2]
        except Exception:
            logger.error("[yaowang] 无法获取截图尺寸，跳过。")
            return CustomRecognition.AnalyzeResult(box=None, detail="截图异常")

        roi = self._bottom_roi(h, w, bottom_ratio)
        logger.debug(f"[yaowang] 底部聊天栏 ROI: {roi} (图 {w}x{h}, ratio={bottom_ratio})")

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

        hit_box = None
        hit_word = None
        full_announce = ""

        if reco and reco.hit and reco.all_results:
            # 拼接底部聊天栏的整段文字（妖王公告通常为其中某一句）
            texts = []
            for r in reco.all_results:
                if r.text:
                    texts.append(r.text)
                if r.text and any(w in r.text for w in expected):
                    hit_box = r.box
                    hit_word = r.text
            full_announce = " ".join(texts)

        if not hit_box:
            return CustomRecognition.AnalyzeResult(box=None, detail="未识别到妖王")

        logger.info(f"[yaowang] 识别到妖王公告：{hit_word}")

        # 冷却去重：冷却期内只通知一次，防止同一条滚动公告 / 聊天刷屏
        now = time.time()
        if now - self._LAST_NOTIFY_TS >= cooldown:
            self._LAST_NOTIFY_TS = now
            content = (hit_word or full_announce) or "妖王"
            send_message("妖王出现", content)

        return CustomRecognition.AnalyzeResult(box=hit_box, detail=f"识别到妖王：{hit_word}")