"""
MuMu 模拟器渲染模式检查器

仅在识别到 MuMu 模拟器时检查显卡渲染模式是否为 DirectX，
如果不是则停止任务并输出警告。对于其他模拟器（如雷电）自动跳过。
"""
import platform
import os
import json
from pathlib import Path

from maa.agent.agent_server import AgentServer
from maa.tasker import Tasker, TaskerEventSink
from maa.event_sink import NotificationType

from utils.logger import logger
def _find_config_in_vms(vms_dir: Path, port: int | None) -> Path | None:
    """
    在 vms 布局中查找配置文件。
    支持实例目录名精确匹配或索引匹配。
    """
    target_index = None
    if port is not None:
        target_index = (port - 16384) // 32
        logger.debug(f"[vms] 根据端口 {port} 计算实例索引: {target_index}")

    exact_match = None
    first_valid = None

    try:
        entries = list(vms_dir.iterdir())
    except Exception as e:
        logger.debug(f"遍历 vms 目录失败: {e}")
        return None

    for instance_dir in entries:
        if not instance_dir.is_dir():
            continue

        config_file = instance_dir / "configs" / "customer_config.json"
        if not config_file.exists():
            continue

        if first_valid is None:
            first_valid = config_file

        # 精确匹配：目录名以 -{index} 结尾，或包含端口对应索引
        if target_index is not None:
            if instance_dir.name.endswith(f"-{target_index}"):
                exact_match = config_file
                logger.debug(f"[vms] 精确匹配实例目录: {instance_dir}")
                break

    if exact_match:
        return exact_match

    if first_valid:
        if target_index is not None:
            logger.warning(
                f"[vms] 未找到索引 {target_index} 的实例，使用首个有效配置: {first_valid}"
            )
        else:
            logger.debug(f"[vms] 找到配置文件: {first_valid}")
        return first_valid

    return None


def _find_config_in_nx_device(nx_device_dir: Path, port: int | None) -> Path | None:
    """
    在 nx_device 布局中查找配置文件。
    结构可能是：nx_device/版本号/configs/customer_config.json 或 nx_device/版本号/vms/...
    """
    target_index = None
    if port is not None:
        target_index = (port - 16384) // 32
        logger.debug(f"[nx_device] 根据端口 {port} 计算实例索引: {target_index}")

    # 优先检查直接配置路径
    for version_dir in nx_device_dir.iterdir():
        if not version_dir.is_dir():
            continue
        direct_config = version_dir / "configs" / "customer_config.json"
        if direct_config.exists():
            logger.debug(f"[nx_device] 直接找到配置文件: {direct_config}")
            return direct_config

        # 检查该版本目录下是否有 vms 子目录
        vms_sub = version_dir / "vms"
        if vms_sub.exists():
            result = _find_config_in_vms(vms_sub, port)
            if result:
                return result

    # 若以上未找到，尝试将 nx_device_dir 视为 vms 的父目录
    parent_vms = nx_device_dir.parent / "vms"
    if parent_vms.exists():
        return _find_config_in_vms(parent_vms, port)

    return None


def get_adb_info_from_controller(controller) -> tuple[str | None, str | None]:
    """
    从 MAA 控制器获取 ADB 路径和设备地址。
    优先从 controller.info 字典中读取，其次从直接属性获取。
    """
    adb_path = None
    address = None

    # 1. 尝试从 controller.info 字典读取（最新版本数据通常在这里）
    try:
        info = getattr(controller, 'info', None)
        if isinstance(info, dict):
            adb_path = info.get('adb_path')
            address = info.get('adb_serial') or info.get('address') or info.get('serial')
            if adb_path:
                adb_path = str(adb_path)
            if address:
                address = str(address)
                logger.debug(f"从 controller.info 获取到 ADB 信息: {adb_path}, {address}")
    except Exception as e:
        logger.debug(f"从 controller.info 读取失败: {e}")

    # 2. 回退：直接属性读取
    if not adb_path:
        try:
            raw_path = getattr(controller, 'adb_path', None)
            if raw_path:
                adb_path = str(raw_path)
        except Exception:
            pass

    if not address:
        try:
            raw_addr = getattr(controller, 'address', None)
            if raw_addr:
                address = str(raw_addr)
        except Exception:
            pass

    # 3. 再尝试从其他属性补充 address
    if adb_path and not address:
        for attr in ('serial', 'device_serial', 'device', 'adb_serial'):
            try:
                val = getattr(controller, attr, None)
                if val:
                    address = str(val)
                    break
            except Exception:
                pass

    # 4. 若仍无 adb_path，尝试通过 Toolkit 获取
    if not adb_path:
        try:
            from maa.toolkit import Toolkit
            devices = Toolkit.find_adb_devices()
            if devices:
                device = devices[0]
                adb_path = str(device.adb_path)
                if not address:
                    address = device.address
                logger.debug(f"从 Toolkit 获取到 ADB 信息: {adb_path}, {address}")
        except Exception as e:
            logger.debug(f"从 Toolkit 获取 ADB 信息失败: {e}")

    if not adb_path:
        logger.debug("所有方式均无法获取 ADB 路径")

    return adb_path, address
def is_mumu_simulator(adb_path: str) -> bool:
    """
    根据 ADB 路径判断是否为 MuMu 模拟器。
    """
    if not adb_path:
        return False
    # 确保 adb_path 为字符串
    path_lower = str(adb_path).lower()
    keywords = ["mumu", "net ease", "netease"]
    return any(kw in path_lower for kw in keywords)

def find_mumu_install_path(adb_path: str) -> Path | None:
    """
    根据 ADB 路径查找 MuMu 模拟器的安装根目录（优先返回包含 vms 的最顶层）。
    """
    try:
        adb_path = Path(adb_path).resolve()
    except Exception as e:
        logger.debug(f"解析 ADB 路径失败: {adb_path}, 错误: {e}")
        return None

    is_mac = platform.system() == "Darwin"
    current = adb_path.parent
    fallback = None
    found_vms_dir = None

    for i in range(10):
        try:
            # 记录第一个找到的包含 vms 的目录（可能是较深的层级）
            if (current / "vms").is_dir():
                if found_vms_dir is None:
                    found_vms_dir = current
                    logger.debug(f"在第 {i+1} 层发现 vms 目录（暂存）: {current}")

            # Windows: 如果遇到 nx_device，继续向上，因为真正的 vms 通常在上层
            if not is_mac and (current / "nx_device").is_dir():
                logger.debug(f"发现 nx_device 目录，继续向上查找顶层安装目录")

            # 记录备用主程序所在目录
            if fallback is None:
                if is_mac:
                    for exe_name in ["MuMuPlayer", "MuMuManager"]:
                        if (current / exe_name).is_file():
                            fallback = current
                            break
                    if current.suffix == ".app":
                        fallback = current.parent
                else:
                    if (current / "MuMuPlayer.exe").is_file() or (current / "MuMuManager.exe").is_file():
                        fallback = current
                        logger.debug(f"发现主程序文件，备用根目录: {current}")

                if (current / "emulator").is_dir():
                    logger.debug(f"发现 emulator 目录（MuMu 5.0），安装根目录: {current}")
                    return current

        except PermissionError:
            pass
        except Exception as e:
            logger.debug(f"检查目录 {current} 时出错: {e}")

        # 如果已经找到 vms 且当前目录名为 "MuMuPlayer-12.0" 或包含 "mumu"，优先返回此处
        if found_vms_dir and any(kw in current.name.lower() for kw in ["mumu", "player"]):
            logger.debug(f"在包含 vms 的合理父目录停止: {current}")
            return current

        current = current.parent

    # 如果循环结束仍未返回，使用找到的第一个包含 vms 的目录
    if found_vms_dir:
        logger.debug(f"最终使用暂存的 vms 所在目录: {found_vms_dir}")
        return found_vms_dir

    # macOS 兜底
    if is_mac:
        support = Path.home() / "Library" / "Application Support"
        for cand in ["MuMuPlayer12", "MuMuPlayer", "MuMu"]:
            if (support / cand / "vms").is_dir():
                return support / cand

    if fallback:
        logger.debug(f"未找到 vms，使用备用目录: {fallback}")
        return fallback

    logger.debug("未找到任何 MuMu 特征目录或文件")
    return None


def extract_port_from_address(address: str) -> int | None:
    """
    从 ADB 地址中提取端口号，例如 '127.0.0.1:16416' -> 16416。
    """
    if ":" in address:
        try:
            return int(address.split(":")[-1])
        except ValueError:
            pass
    return None


def find_config_file(install_path: Path, address: str | None = None) -> Path | None:
    """
    在 MuMu 安装/数据目录中查找 customer_config.json。
    优先检查 install_path 下的 vms，再检查 nx_device，最后检查上层 vms。
    """
    is_mac = platform.system() == "Darwin"

    # macOS 重定向到 Application Support
    if is_mac:
        mac_support = Path.home() / "Library" / "Application Support"
        for cand in ["MuMuPlayer12", "MuMuPlayer", "MuMu"]:
            if (mac_support / cand / "vms").is_dir():
                install_path = mac_support / cand
                break

    port = extract_port_from_address(address) if address else None

    # 1. 首先尝试 install_path 下的 vms（最常见）
    vms_dir = install_path / "vms"
    if vms_dir.exists():
        result = _find_config_in_vms(vms_dir, port)
        if result:
            return result

    # 2. 检查 install_path 下的 nx_device 布局
    nx_device_dir = install_path / "nx_device"
    if nx_device_dir.exists():
        result = _find_config_in_nx_device(nx_device_dir, port)
        if result:
            return result

    # 3. 若 install_path 是 nx_device/版本号 这样的深层目录，尝试向上找 vms
    if not vms_dir.exists() and install_path.parent.name.lower().startswith("mumu"):
        upper_vms = install_path.parent / "vms"
        if upper_vms.exists():
            result = _find_config_in_vms(upper_vms, port)
            if result:
                return result

    logger.debug("未找到任何 customer_config.json 文件")
    return None

def get_render_mode(config_path: Path) -> str | None:
    """
    从 MuMu 配置文件中读取当前渲染模式。
    返回字符串如 "DirectX"、"Vulkan" 或 None（读取失败）。
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error(f"MuMu 配置文件不存在: {config_path}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"MuMu 配置文件解析失败: {e}")
        return None

    render = config.get("setting", {}).get("render", {})
    mode_choose = render.get("mode", {}).get("choose")
    if not mode_choose:
        logger.debug("未找到渲染模式选择字段")
        return None

    backend_key = mode_choose.split(".")[-1]
    backend_value = render.get("mode", {}).get(backend_key)
    if backend_value:
        logger.debug(f"检测到渲染模式: {backend_value} (choose={mode_choose})")
        return backend_value

    logger.debug(f"无法从配置中提取渲染模式后端，choose={mode_choose}")
    return None




@AgentServer.tasker_sink()
class MuMuRenderChecker(TaskerEventSink):
    """
    MuMu 模拟器渲染模式检查器
    """

    def __init__(self):
        super().__init__()
        self._checked = False

    def on_tasker_task(
        self,
        tasker: Tasker,
        noti_type: NotificationType,
        detail: TaskerEventSink.TaskerTaskDetail,
    ):
        try:
            self._do_check(tasker, noti_type, detail)
        except Exception as e:
            logger.exception(f"渲染模式检查器发生未预期异常: {e}")
            tasker.post_stop()

    def _do_check(
        self,
        tasker: Tasker,
        noti_type: NotificationType,
        detail: TaskerEventSink.TaskerTaskDetail,
    ):
        if noti_type != NotificationType.Starting:
            return

        if detail.entry == "MaaTaskerPostStop":
            logger.debug("收到 PostStop 事件，跳过渲染模式检查")
            return

        logger.debug(
            f"任务开始前检查渲染模式 - task_id: {detail.task_id}, entry: {detail.entry}"
        )

        controller = tasker.controller
        if controller is None:
            logger.error("无法获取控制器，跳过渲染模式检查")
            return

        adb_path, address = get_adb_info_from_controller(controller)

        if not adb_path:
            logger.warning("无法获取 ADB 路径，跳过渲染模式检查")
            return

        logger.debug(f"获取到 ADB 路径: {adb_path}")

        if not is_mumu_simulator(adb_path):
            logger.debug("非 MuMu 模拟器，跳过渲染模式检查")
            return

        logger.debug("检测到 MuMu 模拟器，开始检查渲染模式")

        install_path = find_mumu_install_path(adb_path)
        if install_path is None:
            logger.error(
                "🚨 无法定位 MuMu 模拟器安装路径，请确保 MuMu 模拟器已正确安装。"
            )
            tasker.post_stop()
            return

        logger.debug(f"MuMu 安装路径: {install_path}")

        config_path = find_config_file(install_path, address)
        if config_path is None:
            logger.error(
                f"🚨 在安装目录中未找到 MuMu 配置文件。"
                f"安装路径: {install_path}。"
                f"请确保 MuMu 模拟器已正确安装并至少运行过一次。"
            )
            tasker.post_stop()
            return

        render_mode = get_render_mode(config_path)
        if render_mode is None:
            logger.error(
                "🚨 无法获取 MuMu 模拟器渲染模式，请检查配置文件是否完整。"
            )
            tasker.post_stop()
            return

        if render_mode != "DirectX":
            logger.error(
                f"🚨 MuMu 模拟器渲染模式不是 DirectX！任务已停止。"
                f"当前 ADB 地址: {address} 的渲染模式: {render_mode}。"
                f"请打开 MuMu 设置中心 -> 显示 -> 渲染模式，选择“DirectX”并重启模拟器。"
            )
            tasker.post_stop()
        else:
            logger.debug(f"渲染模式检查通过: DirectX (ADB 地址: {address})")