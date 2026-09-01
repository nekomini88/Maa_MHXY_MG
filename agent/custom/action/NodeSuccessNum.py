from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
from utils import logger
from utils import LocalStorage

@AgentServer.custom_action("input_node_success_num")
class input_node_success_num(CustomAction):
    """
   统计节点成功次数
    """
    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> CustomAction.RunResult:
        # 获取次数
        num = LocalStorage.get("node_success", "num")
        # 增加次数
        num += 1
        # 保存次数
        LocalStorage.set("node_success", "num", num)
        
        return CustomAction.RunResult(success=True)

@AgentServer.custom_action("output_node_success_num")
class output_node_success_num(CustomAction):
    """
   输出节点成功次数
    """
    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> CustomAction.RunResult:
       
        # 获取次数参数
        num = LocalStorage.get("node_success", "num")
        # 输出次数
        logger.info(f"运行次数: {num}")
        # 重置次数
        LocalStorage.set("node_success", "num", 0)
        
        return CustomAction.RunResult(success=True)