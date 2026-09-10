# -*- coding: utf-8 -*-
"""日志体积策略：轮转阈值 / 保留期 / 压缩格式，以及启动时的一次性清理。

为什么单独抽出来：以前每个 sink 各写一套参数（妖王日志是 ``rotation="1 day"
+ retention="3 days"`` 且不压缩，日期日志是 ``2 weeks``），既占地方又不一致。
``debug/custom`` 下同时有日志和压缩包，排查时容易堆到几十上百 MB。

当前策略（用户要求）：**单文件超过 20 MB 就轮转，轮转后压缩成 zip，只保留 7 天**。

``prune_old_logs()`` 负责处理**策略生效前就已经存在**的旧日志：压缩较旧的、
删掉过期的（loguru 的 retention 只管它自己轮转出来的文件）。
"""

import os
import time
import zipfile
from typing import List, Optional, Tuple

# 单文件轮转阈值：超过就换新文件，避免一个日志涨到几百 MB
ROTATION = "20 MB"
# 保留期：超过就删除（含 .zip）
RETENTION = "7 days"
RETENTION_DAYS = 7
# 轮转后的归档格式
COMPRESSION = "zip"

LOG_DIR = os.path.join("debug", "custom")
# 正在写入的文件，绝不碰（Windows 上被占用的文件删/压都会报错）
_ACTIVE_FILES = ("yaowang.log",)

_DAY_SECONDS = 24 * 60 * 60


def sink_kwargs() -> dict:
    """给 ``logger.add()`` 用的轮转/保留/压缩参数，保证各模块口径一致。"""
    return {
        "rotation": ROTATION,
        "retention": RETENTION,
        "compression": COMPRESSION,
    }


def prune_old_logs(
    log_dir: Optional[str] = None,
    retention_days: int = RETENTION_DAYS,
    min_age_hours: int = 1,
    compress: bool = True,
    now: Optional[float] = None,
) -> Tuple[int, int, List[str]]:
    """压缩较旧的日志、删除超过保留期的日志。

    Args:
        log_dir: 日志目录（默认 ``debug/custom``）。
        retention_days: 保留天数，超过即删除（压缩包一并删）。
        min_age_hours: 多久没更新的日志才压缩——正在写的文件不能被压。
        compress: 是否压缩较旧但未过期的日志。
        now: 当前时间戳（测试用）。
    Returns:
        ``(压缩数量, 删除数量, 出错的文件名列表)``，任何单文件错误都不会抛出。
    """
    directory = log_dir or LOG_DIR
    if not os.path.isdir(directory):
        return 0, 0, []

    now = time.time() if now is None else now
    today_log = time.strftime("%Y-%m-%d", time.localtime(now)) + ".log"
    # 今天正在写的文件 + 常驻的妖王日志，都不动
    skip = set(_ACTIVE_FILES) | {today_log}

    compressed = deleted = 0
    errors = []
    try:
        names = sorted(os.listdir(directory))
    except Exception as e:  # 目录不可读
        return 0, 0, [f"{directory}: {e}"]

    for name in names:
        path = os.path.join(directory, name)
        if not os.path.isfile(path) or name in skip:
            continue
        is_archive = name.endswith(".zip")
        is_log = name.endswith(".log")
        if not (is_archive or is_log):
            continue
        try:
            age = now - os.path.getmtime(path)
            if age > retention_days * _DAY_SECONDS:
                os.remove(path)
                deleted += 1
            elif compress and is_log and age > min_age_hours * 3600:
                with zipfile.ZipFile(path + ".zip", "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.write(path, arcname=name)
                os.remove(path)
                compressed += 1
        except Exception as e:
            errors.append(f"{name}: {e}")
    return compressed, deleted, errors
