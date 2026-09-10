# -*- coding: utf-8 -*-

import os
import sys
import json
import subprocess
from pathlib import Path

# utf-8
sys.stdout.reconfigure(encoding="utf-8")

# 获取当前main.py路径并设置上级目录为工作目录
current_file_path = os.path.abspath(__file__)
current_script_dir = os.path.dirname(current_file_path)  # 包含此脚本的目录
project_root_dir = os.path.dirname(current_script_dir)  # 假定的项目根目录

# 更改CWD到项目根目录。这对于相对路径至关重要。
if os.getcwd() != project_root_dir:
    os.chdir(project_root_dir)
print(f"set cwd: {os.getcwd()}")

# 将脚本自身的目录添加到sys.path，以便导入utils、maa等模块（如果存在）。
if current_script_dir not in sys.path:
    sys.path.insert(0, current_script_dir)

# 导入logger模块
try:
    from utils import logger
except ImportError:
    import logging
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
    )
    logger = logging

VENV_NAME = ".venv"  # 虚拟环境目录的名称
VENV_DIR = Path(project_root_dir) / VENV_NAME

# requirements.txt 的顶层发行包名。用于判断依赖是否已随包预装：
# 齐全时完全不调用 pip，离线/被墙的机器也不会再刷“镜像源不可用”的假故障。
REQUIRED_DISTRIBUTIONS = (
    "colorama",
    "levenshtein",
    "loguru",
    "maaagentbinary",
    "maafw",
    "numpy",
    "pillow",
    "python-levenshtein",
    "rapidfuzz",
    "strenum",
    "requests",
    "pydantic-core",
    "opencv-python-headless",
    "scikit-image",
    "zai-sdk",
)

# 包内离线 wheel 目录（按优先级查找，命中即用 --no-index 安装，不联网）
LOCAL_WHEEL_DIRS = ("deps", "install/deps", "wheels")


def _is_running_in_our_venv():
    """检查脚本是否在此脚本管理的特定venv中运行。"""
    # 检查sys.executable是否以我们VENV_DIR的绝对路径开头
    # 如果其他venv可能处于活动状态，这比sys.prefix != sys.base_prefix更可靠
    return sys.executable.startswith(str(VENV_DIR.resolve() / "bin"))


def ensure_linux_venv_and_relaunch_if_needed():
    """
    在Linux上，确保venv存在，并且如果尚未在脚本管理的venv中运行，
    则在其中重新启动脚本。
    """
    if sys.platform.startswith("linux"):
        logger.info(f"检测到Linux系统。当前Python解释器: {sys.executable}")

        if _is_running_in_our_venv():
            logger.info(f"已在托管的虚拟环境 ({VENV_DIR}) 中运行。")
            return

        logger.info(
            f"未在托管的虚拟环境中运行。如果需要，将设置 {VENV_DIR} 并重新启动。"
        )

        if not VENV_DIR.exists():
            logger.info(f"正在 {VENV_DIR} 创建虚拟环境...")
            try:
                # 使用当前运行此脚本的Python（系统/外部Python）
                subprocess.run(
                    [sys.executable, "-m", "venv", str(VENV_DIR)],
                    check=True,
                    capture_output=True,
                )
                logger.info(f"虚拟环境 {VENV_DIR} 创建成功。")
            except subprocess.CalledProcessError as e:
                logger.error(
                    f"创建虚拟环境 '{VENV_DIR}' 失败: {e.stderr.decode(errors='ignore') if e.stderr else e.stdout.decode(errors='ignore')}"
                )
                logger.error("在Linux上无法在没有虚拟环境的情况下继续。正在退出。")
                sys.exit(1)
            except FileNotFoundError:
                logger.error(
                    f"命令 '{sys.executable} -m venv' 未找到。请确保 'venv' 模块可用。"
                )
                logger.error("在Linux上无法在没有虚拟环境的情况下继续。正在退出。")
                sys.exit(1)

        python_in_venv = VENV_DIR / "bin" / "python"
        if not python_in_venv.exists():
            logger.error(f"在虚拟环境 {python_in_venv} 中未找到Python解释器。")
            logger.error("虚拟环境创建可能失败或虚拟环境结构异常。")
            sys.exit(1)

        logger.info(f"正在使用虚拟环境中的Python ({python_in_venv}) 重新启动脚本。")
        try:
            # os.execv替换当前进程
            os.execv(str(python_in_venv), [str(python_in_venv)] + sys.argv)
        except Exception as e:
            logger.exception(f"在虚拟环境中重新启动脚本失败: {e}")
            sys.exit(1)
    else:
        logger.info(f"当前系统 ({sys.platform})，跳过虚拟环境设置和重新启动。")


def read_pip_config() -> dict:
    # 必须锚定项目根目录：原来用 "./config"（跟随 CWD），而
    # update_pip_config_last_version 用的是 project_root_dir/config，
    # 两者不一致时会读写到两个不同的 pip_config.json。
    config_dir = Path(project_root_dir) / "config"
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "pip_config.json"
    default_config = {
        "enable_pip_install": True,
        "last_version": "unknown",
        "mirror": "https://mirrors.ustc.edu.cn/pypi/simple",
        "backup_mirrors": [
            "https://pypi.tuna.tsinghua.edu.cn/simple",
            "https://mirrors.cloud.tencent.com/pypi/simple/",
            "https://pypi.org/simple",
        ],
    }
    if not config_path.exists():
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4, ensure_ascii=False)
        return default_config
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("读取pip配置失败，使用默认配置")
        return default_config


def installed_distributions() -> set:
    """当前解释器里已安装的发行包名（已规范化）。"""
    try:
        from importlib import metadata as importlib_metadata
    except ImportError:  # pragma: no cover
        return set()

    names = set()
    for dist in importlib_metadata.distributions():
        try:
            name = dist.metadata["Name"]
        except Exception:
            continue
        if name:
            names.add(_normalize_dist_name(name))
    return names


def _normalize_dist_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def missing_requirements(installed=None) -> list:
    """返回 requirements.txt 中尚未安装的发行包（空列表 = 依赖齐全）。"""
    have = installed_distributions() if installed is None else {
        _normalize_dist_name(x) for x in installed
    }
    return [d for d in REQUIRED_DISTRIBUTIONS if _normalize_dist_name(d) not in have]


def find_local_wheel_dir():
    """查找包内随附的离线 wheel 目录，找不到返回 None。"""
    for rel in LOCAL_WHEEL_DIRS:
        candidate = Path(project_root_dir) / rel
        try:
            if candidate.is_dir() and any(candidate.glob("*.whl")):
                return candidate
        except OSError:
            continue
    return None


def check_mirror_reachable(mirror: str, timeout: int = 8) -> bool:
    """用 HTTP 请求探测镜像源是否可达。

    原来用 `python -m pip list -i <mirror>` 探测：pip 缺失/损坏时直接
    返回非零码，会把可用镜像误判为不可用（用户日志里的“镜像源返回错误(代码: 1)”
    就是这么来的）。这里改成标准库 HTTP 探测，不依赖 pip 行为。
    """
    import urllib.error
    import urllib.request

    url = mirror.rstrip("/") + "/"
    request = urllib.request.Request(
        url, method="GET", headers={"User-Agent": "Maa_MHXY_MG-agent/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            return 200 <= int(status) < 400
    except Exception as exc:
        logger.warning(f"镜像源不可用 {mirror}: {exc}")
        return False


def get_available_mirror(pip_config: dict) -> str:
    mirrors = [pip_config.get("mirror")] + pip_config.get("backup_mirrors", [])

    for mirror in filter(None, mirrors):  # 过滤掉None或空字符串
        logger.info(f"尝试连接镜像源: {mirror}")
        if check_mirror_reachable(mirror):
            logger.info("当前镜像源可用")
            return mirror
    logger.error("所有镜像源都不可用")
    return None


def _run_pip_command(cmd_args: list, operation_name: str) -> bool:
    try:
        logger.info(f"开始 {operation_name} (使用Python: {sys.executable})...")
        process = subprocess.Popen(
            cmd_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout, stderr = process.communicate()

        if process.returncode == 0:
            logger.info(f"{operation_name} 完成")
            if stdout and stdout.strip():
                logger.debug(
                    f"{operation_name} 标准输出:\n{stdout.strip()}"
                )  # 仅当stdout不为空时记录
            return True
        else:
            logger.error(f"{operation_name} 时出错。返回码: {process.returncode}")
            if stdout and stdout.strip():
                logger.error(f"{operation_name} 标准输出:\n{stdout.strip()}")
            if stderr and stderr.strip():
                logger.error(f"{operation_name} 标准错误:\n{stderr.strip()}")
            return False
    except Exception as e:
        logger.exception(f"{operation_name} 时发生未知异常: {e}")
        return False


def install_requirements(req_file="requirements.txt", pip_config=None) -> bool:
    req_path = Path(project_root_dir) / req_file  # 确保相对于项目根目录
    if not req_path.exists():
        logger.error(f"{req_file} 文件不存在于 {req_path.resolve()}")
        return False

    # 优先级 1：包内离线 wheel。命中时用 --no-index，全程不联网，
    # 离线机器/被墙机器也能把依赖补齐（用户端不再依赖镜像源可达）。
    wheel_dir = find_local_wheel_dir()
    if wheel_dir:
        offline_cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-U",
            "--no-index",
            "--find-links",
            str(wheel_dir),
            "--no-warn-script-location",
            "-r",
            str(req_path),
        ]
        if _run_pip_command(offline_cmd, f"从本地离线包 {wheel_dir} 安装依赖"):
            return True
        logger.warning("本地离线包安装失败，回退到在线镜像源")

    # 优先级 2：在线镜像源（仅在无离线包或离线安装失败时使用）
    mirror = get_available_mirror(pip_config or {})
    if not mirror:
        logger.error("没有可用的镜像源，安装依赖失败")
        return False

    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-U",
        "-r",
        str(req_path),
        "--no-warn-script-location",
        "-i",
        mirror,
    ]
    return _run_pip_command(cmd, f"从 {req_path.name} 安装依赖")


def check_and_install_dependencies():
    pip_config = read_pip_config()
    enable_pip_install = pip_config.get("enable_pip_install", True)

    if sys.platform.startswith("linux"):
        logger.info(f"在虚拟环境 ({VENV_DIR}) 中运行: {_is_running_in_our_venv()}")

    current_version = read_interface_version()
    last_version = pip_config.get("last_version", "unknown")

    logger.info(f"启用 pip 安装依赖: {enable_pip_install}")
    logger.info(f"当前资源版本: {current_version}, 上次运行版本: {last_version}")

    # 依赖已随包预装且齐全时直接跳过：完全不调用 pip，
    # 离线/被墙环境也不会再出现“镜像源不可用”的假故障。
    missing = missing_requirements()
    if not missing:
        logger.info("依赖已随包预装且完整，跳过依赖安装。")
        if current_version and current_version != last_version:
            update_pip_config_last_version(current_version)
        return

    logger.warning(f"缺少依赖: {', '.join(missing)}")
    if not enable_pip_install:
        logger.warning("Pip 依赖安装已禁用，缺少的依赖不会被自动安装。")
        return

    logger.info("开始安装/更新依赖。")
    if install_requirements(pip_config=pip_config):
        update_pip_config_last_version(current_version)
        logger.info("依赖检查和安装完成。")
    else:
        logger.warning("依赖安装失败，程序可能无法正常运行。")


def read_interface_version(interface_file_name="./interface.json") -> str:
    interface_path = Path(project_root_dir) / interface_file_name
    assets_interface_path = Path(project_root_dir) / "assets" / interface_file_name

    target_path = None
    if interface_path.exists():
        target_path = interface_path
    elif assets_interface_path.exists():
        logger.info("当前处于开发模式")
        return "unknown"

    if target_path is None:
        logger.warning("未找到interface.json")
        return "unknown"

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            interface_data = json.load(f)
            return interface_data.get("version", "unknown")
    except Exception:
        logger.exception(f"读取interface.json版本失败，文件路径：{target_path}")
        return "unknown"


def update_pip_config_last_version(version: str) -> bool:
    config_path = Path(project_root_dir) / "config" / "pip_config.json"
    try:
        config = read_pip_config()
        config["last_version"] = version

        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        return True
    except Exception:
        logger.exception("更新pip配置失败")
        return False


def agent():
    try:
        from utils import logger

        from maa.agent.agent_server import AgentServer
        from maa.toolkit import Toolkit

        import custom

        Toolkit.init_option("./")

        socket_id = sys.argv[-1]
        # socket_id = "abc"
        # print(f"socket_id: {socket_id}")

        AgentServer.start_up(socket_id)
        logger.info("AgentServer 启动")
        AgentServer.join()
        AgentServer.shut_down()
        logger.info("AgentServer 关闭")
    except Exception as e:
        logger.exception("agent 运行过程中发生异常")
        raise


def main():
    ensure_linux_venv_and_relaunch_if_needed()
    logger.info(f"Python解释器: {sys.executable}")
    if sys.platform.startswith("linux"):
        logger.info(f"在虚拟环境 ({VENV_DIR}) 中运行: {_is_running_in_our_venv()}")

    check_and_install_dependencies()
    agent()


if __name__ == "__main__":
    main()
