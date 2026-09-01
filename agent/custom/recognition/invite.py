from maa.agent.agent_server import AgentServer
from maa.custom_recognition  import CustomRecognition
from maa.context import Context
from utils import logger

@AgentServer.custom_recognition("invite")
class invite(CustomRecognition):
    """
    邀请队员
    "attach": {
            "name": ["姓名1", "姓名2"]

        }
        name: 需要要的的队员名字列表。
        

    """
    def analyze(
         self,
         context: Context,
         argv: CustomRecognition.AnalyzeArg,
     ) -> CustomRecognition.AnalyzeResult:
        # 识别搜索位置并点击
        image = context.tasker.controller.post_screencap().wait().get()
        reco_result = context.run_recognition(
            "搜索",
            image,
            pipeline_override={
                "搜索":{"roi" : [336,224,64,38],
                        "expected":["搜索"],
                        "recognition": "OCR"
                       }
                }
        )
        box = [0,0,0,0]
        for res in reco_result.all_results:
            box = res.box
        center_x = box[0] + box[2] // 2
        center_y = box[1] + box[3] // 2 
        context.tasker.controller.post_click(center_x, center_y).wait()
        


       
        return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="活跃度小于50,任务结束")



