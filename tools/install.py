from pathlib import Path

import shutil
import sys
import json

import os
import sys

# Windows 下控制台默认 GBK 编码，print 含中文会抛 UnicodeEncodeError。
# 强制 stdout/stderr 用 UTF-8，避免打包脚本在中文环境下崩溃。
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(script_dir)

from configure import configure_ocr_model

working_dir = Path(__file__).parent.parent
install_path = working_dir / Path("install")
version = len(sys.argv) > 1 and sys.argv[1] or "v0.0.1"
platform_tag = len(sys.argv) > 2 and sys.argv[2] or ""


def install_deps(platform_tag: str):
    """安装 MaaFramework 依赖到对应架构路径

    Args:
        platform_tag: 平台标签，如 win-x64, linux-arm64, osx-arm64
    """
    if not platform_tag:
        raise ValueError("platform_tag is required")

    shutil.copytree(
        working_dir / "deps" / "bin",
        install_path / "runtimes" / platform_tag / "native",
        ignore=shutil.ignore_patterns(
            "*MaaDbgControlUnit*",
            "*MaaThriftControlUnit*",
            "*MaaWin32ControlUnit*",
            "*MaaRpc*",
            "*MaaHttp*",
            "plugins",
            "*.node",
            "*MaaPiCli*",
        ),
        dirs_exist_ok=True,
    )
    shutil.copytree(
        working_dir / "deps" / "share" / "MaaAgentBinary",
        install_path / "libs" / "MaaAgentBinary",
        dirs_exist_ok=True,
    )
    shutil.copytree(
        working_dir / "deps" / "bin" / "plugins",
        install_path / "plugins" / platform_tag,
        dirs_exist_ok=True,
    )




def install_resource():

    configure_ocr_model()

    shutil.copytree(
        working_dir / "assets" / "resource",
        install_path / "resource",
        dirs_exist_ok=True,
    )
    shutil.copy2(
        working_dir / "assets" / "interface.json",
        install_path,
    )

    with open(install_path / "interface.json", "r", encoding="utf-8") as f:
        interface = json.load(f)

    interface["version"] = version

    with open(install_path / "interface.json", "w", encoding="utf-8") as f:
        json.dump(interface, f, ensure_ascii=False, indent=4)


def install_chores():
    for file in ["README.md", "LICENSE", "requirements.txt"]:
        shutil.copy2(
            working_dir / file,
            install_path,
        )
    shutil.copytree(
        working_dir / "docs",
        install_path / "docs",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("*.yaml"),
    )


def install_agent():
    shutil.copytree(
        working_dir / "agent",
        install_path / "agent",
        dirs_exist_ok=True,
    )

    with open(install_path / "interface.json", "r", encoding="utf-8") as f:
        interface = json.load(f)

    if sys.platform.startswith("win"):
        interface["agent"]["child_exec"] = r"./python/python.exe"
    elif sys.platform.startswith("darwin"):
        interface["agent"]["child_exec"] = r"./python/bin/python3"
    elif sys.platform.startswith("linux"):
        interface["agent"]["child_exec"] = r"python3"

    interface["agent"]["child_args"] = [r"./agent/main.py", "-u"]

    with open(install_path / "interface.json", "w", encoding="utf-8") as f:
        json.dump(interface, f, ensure_ascii=False, indent=4)


def install_config():
    """把 config/config.json 模板复制进包，确保外部通知配置键存在。

    程序包内默认不含 config.json，导致 send_message 读不到
    ExternalNotificationEnabled 而静默跳过发送。此函数把模板复制进包，
    用户只需在包内 config/config.json 填 Telegram token/chat_id 即可启用通知。
    """
    src = working_dir / "config" / "config.json"
    if not src.exists():
        print("[install] config/config.json template not found, skip config template install.")
        return
    dst_dir = install_path / "config"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "config.json"
    if dst.exists():
        # 不覆盖用户已有的配置
        print("[install] config/config.json already exists, keep user config.")
        return
    shutil.copy2(src, dst)
    print(f"[install] Installed config template to {dst}")


def install_pip_config():
    """预置 config/pip_config.json，默认关闭运行时 pip 安装。

    包内已随附全部 Windows wheel 并预装进 embed python，运行时不需要联网装依赖。
    默认关闭可避免离线/被墙的机器在启动时刷「镜像源不可用」并误报依赖安装失败。
    用户若想恢复自动安装，把 enable_pip_install 改成 true 即可。
    """
    dst_dir = install_path / "config"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "pip_config.json"
    if dst.exists():
        print("[install] config/pip_config.json already exists, keep user config.")
        return
    config = {
        "enable_pip_install": False,
        "last_version": version,
        "mirror": "https://mirrors.ustc.edu.cn/pypi/simple",
        "backup_mirrors": [
            "https://pypi.tuna.tsinghua.edu.cn/simple",
            "https://mirrors.cloud.tencent.com/pypi/simple/",
            "https://pypi.org/simple",
        ],
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    print(f"[install] Installed pip config to {dst}")


def install_notify_override():
    """预置 config/notify.json（我们自己的明文通知配置，MFA 不管理它）。

    为什么需要它：config/config.json 是 MFAAvalonia 的 "Default" 配置本体
    （MFA 源码 ConfigurationManager：Default → 文件名 config），用户在 MFA
    「设置 → 外部通知 → Telegram」里填的 token/chat_id 会被 MFA 用 DPAPI 加密
    后写回那个文件，只有填值那台机器能解开。agent 会尝试自动解密；解不开时，
    把明文写进这个 MFA 不碰的文件即可（非空值优先）。
    """
    dst_dir = install_path / "config"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "notify.json"
    if dst.exists():
        print("[install] config/notify.json already exists, keep user config.")
        return
    config = {
        "_说明": [
            "Agent 的明文 Telegram 通知配置，MFAAvalonia 不管理这个文件。",
            "留空则使用 config/config.json 里的值（MFA 加密值会被 agent 自动解密）。",
            "只有自动解密失败时（例如配置是从别的电脑拷来的）才需要在这里填明文。",
            "ExternalNotificationTelegramBotToken：BotFather 给的 token，形如 123456789:AAH...",
            "ExternalNotificationTelegramChatId：接收通知的数字 ID（自己的用户 ID 为正数）",
        ],
        "ExternalNotificationTelegramBotToken": "",
        "ExternalNotificationTelegramChatId": "",
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    print(f"[install] Installed notify override template to {dst}")


def install_notify_checker():
    """把 Telegram 通知自检脚本与一键 bat 放进包内。

    用户填完 config/config.json 后不必启动游戏，双击 bat 即可知道配置对不对。
    """
    src = working_dir / "tools" / "check_notify.py"
    if not src.exists():
        print("[install] tools/check_notify.py not found, skip notify checker.")
        return
    dst_dir = install_path / "tools"
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst_dir / "check_notify.py")

    bat = install_path / "检查通知配置.bat"
    bat.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        'cd /d "%~dp0"\r\n'
        "echo === Telegram 通知配置自检 ===\r\n"
        "echo.\r\n"
        "python\\python.exe tools\\check_notify.py\r\n"
        "echo.\r\n"
        "pause\r\n",
        encoding="utf-8",
    )
    print(f"[install] Installed notify checker to {dst_dir} and {bat.name}")


if __name__ == "__main__":
    install_deps(platform_tag)
    install_resource()
    install_chores()
    install_agent()
    install_config()
    install_pip_config()
    install_notify_override()
    install_notify_checker()

    print(f"Install to {install_path} successfully.")
