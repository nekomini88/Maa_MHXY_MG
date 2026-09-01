# -*- coding: utf-8 -*-
"""妖王出现识别 + 通知。

自定义识别器 ``yaowang``：对屏幕做 OCR，检测「妖王」关键词（挂机/抓鬼时出现的横幅提示、
聊天气泡、系统公告等位置）。命中后：

- 调用 ``utils.send_message`` 往 Telegram 推送「妖王出现」通知；
- 带冷却（``yaowang_cooldown_seconds``），短时间内重复命中不重复刷屏；
- 返回 ``AnalyzeResult(box=命中框, detail=...)`` 以供 pipeline 后续跳转（如点击剿灭）。

配置（config/config.json）：
    yaowang_rois:           OCR 检测区域列表 [x,y,w,h]（可多个区域，默认覆盖中央横幅+全屏）
    yaowang_cooldown_seconds: 两次通知之间的最小间隔秒数（默认 300，即 5 分钟）
    yaowang_expected:       要匹配的关键词（默认 ["妖王"]）
"""

import json
import time

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition
from maa.context import Context

from utils import logger
from utils import send_message

DEFAULT_ROIS = [
    # 妖王提示出现在屏幕【最底下部通知栏】——覆盖底部通栏大范围
    [0, 360, 1000, 220],    # 底部通知栏 (y 360~580)
    [0, 0, 1000, 300],      # 顶部横幅区(兜底)
]
DEFAULT_COOLDOWN = 300      # 秒
# 默认匹配词。OCR 可能把「妖王」误读成近形/近音字，故给一组常见变体兜底。
# 精准匹配词优先（妖王），变体用于容错；越低越宽，注意误报。
DEFAULT_EXPECTED = ["妖王", "妖玉", "妖互", "受王", "妖主", "要王"]


@AgentServer.custom_recognition("yaowang")
class Yaowang(CustomRecognition):
    """识别屏幕上的「妖王」提示并发送通知。"""

    _ROC_NAME = "yaowang-ocr"
    _LAST_NOTIFY = {}       # 按 expected 词记忆最近通知时间戳

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult:
        param: dict = json.loads(argv.custom_recognition_param or "{}")

        rois: list = param.get("yaowang_rois", DEFAULT_ROIS)
        cooldown: int = int(param.get("yaowang_cooldown_seconds", DEFAULT_COOLDOWN))
        expected: list = param.get("yaowang_expected", DEFAULT_EXPECTED)

        image = context.tasker.controller.post_screencap().wait().get()

        hit_box = None
        hit_word = None

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
            if reco and reco.hit and reco.all_results:
                # 取命中项里最靠中心的一个作为锚点
                for r in reco.all_results:
                    if r.text and any(w in r.text for w in expected):
                        hit_box = r.box
                        hit_word = r.text
                        break
            if hit_box:
                break

        if not hit_box:
            return CustomRecognition.AnalyzeResult(box=None, detail="未识别到妖王")

        logger.info(f"[yaowang] 识别到妖王提示：{hit_word} box={hit_box}")

        # 冷却去重：同一关键词短时间内只通知一次
        now = time.time()
        last = self._LAST_NOTIFY.get(hit_word, 0)
        if now - last >= cooldown:
            self._LAST_NOTIFY[hit_word] = now
            send_message("妖王出现", f"识别到妖王：{hit_word}")

        return CustomRecognition.AnalyzeResult(box=hit_box, detail=f"识别到妖王：{hit_word}")