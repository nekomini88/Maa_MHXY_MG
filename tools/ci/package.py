#!/usr/bin/env python3
"""把 install/ 目录打成可分发的 Release 包。

排除 install/deps（离线 wheel 仓库，约 140MB）：依赖已经预装进 embed python
的 site-packages，运行时用不到它们；留在包里只会让每个用户多下 135MB。

用法: python3 tools/ci/package.py [install 目录] [输出 zip]
"""

import os
import sys
import zipfile

DEFAULT_INSTALL_DIR = "install"
DEFAULT_OUTPUT = "Maa_MHXY_MG-win-x86_64-MFAA.zip"
EXCLUDE_TOP_LEVEL = ("deps",)


def build(install_dir=DEFAULT_INSTALL_DIR, out_path=DEFAULT_OUTPUT, exclude=EXCLUDE_TOP_LEVEL):
    install_dir = os.path.abspath(install_dir)
    if not os.path.isdir(install_dir):
        raise FileNotFoundError(f"install 目录不存在: {install_dir}")

    file_count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk(install_dir):
            if os.path.relpath(root, install_dir) == ".":
                dirs[:] = [d for d in dirs if d not in exclude]
            for name in files:
                full_path = os.path.join(root, name)
                archive.write(full_path, os.path.relpath(full_path, install_dir))
                file_count += 1

    return file_count, os.path.getsize(out_path)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    install_dir = argv[1] if len(argv) > 1 else DEFAULT_INSTALL_DIR
    out_path = argv[2] if len(argv) > 2 else DEFAULT_OUTPUT
    count, size = build(install_dir, out_path)
    print(f"[package] {out_path}: {count} 个文件, {size / 1024 / 1024:.1f} MB")
    print(f"[package] 已排除顶层目录: {', '.join(EXCLUDE_TOP_LEVEL)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
