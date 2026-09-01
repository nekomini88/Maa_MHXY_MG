from maa.custom_action import CustomAction

from typing import Dict, Any
import os
import json
import re

# ===== 多实例数据隔离 (MaaFramework Project Interface v2.5.0) =====
# 文档: docs/3.3-ProjectInterfaceV2协议.md -> "Agent 子进程环境变量"
# Client 在启动 agent 子进程时注入 PI_CONTROLLER（单行 JSON，当前选中的 controller 对象，
# i18n 已完成解析）。读取不到或解析失败时回退到 DEFAULT_INSTANCE_ID，
# 保证单实例 / 未注入环境（如裸跑 python agent/main.py）下兼容运行。
DEFAULT_INSTANCE_ID = "default"


def get_instance_id() -> str:
    """从 PI_CONTROLLER 环境变量提取当前实例的唯一标识。

    对本项目的 Adb 控制器，不同模拟器实例的 adb.address（含端口，如 127.0.0.1:16416）
    天然不同，优先用作实例 key；其次回退到 controller.name；均不可得时返回默认值。

    注意：不使用 hash() 作为 key，因其每次进程运行结果不稳定，无法跨实例稳定区分多开。
    """
    raw = os.environ.get("PI_CONTROLLER", "")
    if not raw:
        return DEFAULT_INSTANCE_ID

    try:
        ctrl = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return DEFAULT_INSTANCE_ID

    if not isinstance(ctrl, dict):
        return DEFAULT_INSTANCE_ID

    # 优先取 adb 地址（含端口），兼容顶层 address 与 adb.address 两种布局
    address = ctrl.get("address")
    adb = ctrl.get("adb")
    if isinstance(adb, dict):
        address = adb.get("address") or address
    if address:
        return f"adb::{address}"

    name = ctrl.get("name")
    if name:
        return f"ctrl::{name}"

    return DEFAULT_INSTANCE_ID


# 本地存储
class LocalStorage:
    # 存储文件路径
    agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_dir = os.path.join(agent_dir, "data")
    storage_path = os.path.join(config_dir, "mnma_storage.json")

    @staticmethod
    def _scoped_key(task: str) -> str:
        """为 task 分组键加上实例前缀，实现多实例下的数据隔离。

        例如：node_success -> adb::127.0.0.1:16416::node_success
        不同实例写同一个 mnma_storage.json 时，彼此的状态互不干扰。
        """
        return f"{get_instance_id()}::{task}"

    # 检查并确保存储文件存在
    @classmethod
    def ensure_storage_file(cls):
        # 确保配置目录存在
        if not os.path.exists(cls.config_dir):
            os.makedirs(cls.config_dir)

        # 确保存储文件存在
        if not os.path.exists(cls.storage_path):
            with open(cls.storage_path, "w") as f:
                json.dump({}, f)

    # 读取存储数据
    @classmethod
    def read(cls) -> dict:
        cls.ensure_storage_file()
        try:
            with open(cls.storage_path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            # 存储文件格式错误时重置为空对象
            with open(cls.storage_path, "w") as f:
                json.dump({}, f)
            return {}

    # 获取存储值
    @classmethod
    def get(cls, task: str, key: str) -> str | bool | int | None:
        storage = cls.read()
        task_storage = storage.get(cls._scoped_key(task))
        if task_storage is None:
            return None
        return task_storage.get(key)

    # 写入存储数据到文件
    @classmethod
    def write(cls, storage: dict) -> bool:
        try:
            with open(cls.storage_path, "w") as f:
                json.dump(storage, f)
            return True
        except Exception as e:
            print(f"存储数据时出错: {e}")
            return False

    # 设置存储值
    @classmethod
    def set(cls, task: str, key: str, value: str | bool | int) -> bool:
        storage = cls.read()
        scoped = cls._scoped_key(task)
        if scoped not in storage:
            storage[scoped] = {}
        storage[scoped][key] = value

        return cls.write(storage)