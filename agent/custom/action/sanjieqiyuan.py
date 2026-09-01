import os
import json
from datetime import datetime

from PIL import Image
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

from utils import logger

@AgentServer.custom_action("sanjie")
class sanjie(CustomAction):
    """
    三界奇缘答题，
        1.图片
        2.ocr
        3.搜索答案
        4.识别答案位置
        5.return 答案位置
    """
    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> CustomAction.RunResult:
        
        logger.info("进入sanjie")
        context.tasker.controller.post_click(34, 137).wait()
        return CustomAction.RunResult(success=True)


        # box = argv.reco_detail.box
        # if box:
        #     x = (box[0] + box[2]) // 2
        #     y = (box[1] + box[3]) // 2
        #     context.tasker.controller.post_click(x, y).wait()
        #     return CustomAction.RunResult(success=True)
        # else:
        #     return CustomAction.RunResult(success=False)

