from maa.agent.agent_server import AgentServer
from maa.custom_recognition  import CustomRecognition
from maa.context import Context
from utils import logger
import re


@AgentServer.custom_recognition("OCRNum")
class OCRNum(CustomRecognition):
    def analyze(
         self,
         context: Context,
         argv: CustomRecognition.AnalyzeArg,
     ) -> CustomRecognition.AnalyzeResult:
        """
        获取活跃度并判断
        """
        logger.info("OCRNum")
        image1 = context.tasker.controller.post_screencap().wait().get()
        recoNum =  context.run_recognition(
            "识别活跃度",
            image1,
            pipeline_override={
                "识别活跃度":{"roi" : [305,586,862,70],
                              "expected":[""],
                              "recognition": "OCR"
                            }
                }
            )
        logger.info(f"识别结果: {recoNum}")
        if not recoNum or not recoNum.hit:
                logger.info("没有识别到活跃度")

                return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="没有识别到活跃度")
        for res in recoNum.all_results:
            # num = int(res.text)
            
            num = OCRNum.convert_to_int(res.text)
            if num >= 50:
                context.run_task("活动-运镖-点击日常活动")
                return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="活跃度大于50")
            else:
                context.run_task("panduan_zhujiemian")
                return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="活跃度小于50,任务结束")

    def convert_to_int(s):
        try:
            num = int(s)
            logger.info("活跃度为: %s", num)
            return num
        except ValueError:
            logger.info("识别活跃度错误", s)
            return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="识别活跃度错误")


@AgentServer.custom_recognition("OCRVitality")
class OCRVitality(CustomRecognition):
    def analyze(
         self,
         context: Context,
         argv: CustomRecognition.AnalyzeArg,
     ) -> CustomRecognition.AnalyzeResult:
        """
        识别活力，并判断点击打工次数
        "recommended roi" : [380,103,542,46]
        """
        logger.info("进入识别活力agnet")
        image1 = context.tasker.controller.post_screencap().wait().get()
        recoNum =  context.run_recognition(
            "识别活力",
            image1,
            pipeline_override={
                "识别活力":{"roi" :  [380,103,542,46],
                              "expected":[""],
                              "recognition": "OCR"
                            }
                }

            )
        if not recoNum or not recoNum.hit:
            logger.info("没有识别到活力")
            return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="没有识别到活力")
        for res in recoNum.all_results:
            strnum = re.match(r'^(\d+)/', res.text)
            huoli = int(strnum.group(1))
            logger.info(f"活力为: {huoli}")
            # 点击次数
            num=huoli // 100
            if num == 0:
                logger.info("活力不足，无需打工")
                return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="活力不足，无需打工")
            else:
                for i in range(num):
                    context.run_task("点击打工")
        return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="活力打工完成")
