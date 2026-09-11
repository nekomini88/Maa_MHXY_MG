#!/usr/bin/env python3
"""校验打包产物 zip 是否真的带上了预装依赖与 pip 配置。

CI 上传 Release 之前的最后一道门禁。历史上出现过「CI 步骤失败但
asset 仍是旧包」的情况：用户下载到的包里没有 python/Lib/site-packages，
启动时只能联网 pip，于是刷出一片「镜像源返回错误」。

用法: python3 tools/ci/verify_package.py <zip 路径>
"""

import sys
import zipfile

# zip 内必须存在的前缀（目录以 / 结尾）或完整路径
REQUIRED_PREFIXES = (
    "python/Lib/site-packages/numpy/",
    "python/Lib/site-packages/cv2/",
    "python/Lib/site-packages/maa/",
    "python/Lib/site-packages/PIL/",
    "python/python.exe",
    "agent/main.py",
    "agent/utils/notify_config.py",
    "agent/utils/mfa_crypto.py",
    "agent/utils/log_policy.py",
    "agent/utils/link_guard.py",
    "tools/check_notify.py",
    "config/pip_config.json",
    "config/notify.json",
    "interface.json",
)


def verify(zip_path: str):
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
    except (FileNotFoundError, zipfile.BadZipFile) as exc:
        return False, f"无法读取 zip: {exc}"

    missing = [
        prefix
        for prefix in REQUIRED_PREFIXES
        if not any(name == prefix or name.startswith(prefix) for name in names)
    ]
    if missing:
        return False, f"包内缺少: {', '.join(missing)}"
    return True, f"OK: {len(names)} 个文件，预装依赖与 pip_config 均在包内"


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 2:
        print("用法: python3 tools/ci/verify_package.py <zip 路径>")
        return 2
    ok, message = verify(argv[1])
    print(("[OK] " if ok else "[FAIL] ") + message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
