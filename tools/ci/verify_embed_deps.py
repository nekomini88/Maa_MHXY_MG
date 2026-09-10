#!/usr/bin/env python3
"""校验 embed python 的依赖是否已离线预装完整。

打包流程的门禁：预装不完整时直接退出码 1，避免又发出一个
「用户端启动时要联网 pip 装依赖 / 刷镜像源报错」的包。

用法: python3 tools/ci/verify_embed_deps.py [install/python/Lib/site-packages]
"""

import glob
import os
import sys

# 顶层包目录名（import 名），这些必须在 site-packages 里出现
REQUIRED_PACKAGES = (
    "numpy",
    "cv2",
    "PIL",
    "maa",
    "requests",
    "loguru",
    "colorama",
    "skimage",
    "scipy",
    "rapidfuzz",
    "Levenshtein",
)

# requirements.txt 共 15 个顶层发行包，配套依赖装完远多于这个数；
# 阈值取 25 用于兜住「只装了一半」的情况。
MIN_DIST_INFO = 25


def verify(site_packages: str):
    site_packages = os.path.abspath(site_packages)
    if not os.path.isdir(site_packages):
        return False, f"目录不存在: {site_packages}"

    entries = set(os.listdir(site_packages))
    missing = [p for p in REQUIRED_PACKAGES if p not in entries]
    dist_count = len(glob.glob(os.path.join(site_packages, "*.dist-info")))

    if missing:
        return False, f"缺少包: {', '.join(missing)}"
    if dist_count < MIN_DIST_INFO:
        return False, f"dist-info 数量不足: {dist_count} < {MIN_DIST_INFO}"
    return True, f"OK: {len(entries)} 个条目, {dist_count} 个 dist-info"


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    target = argv[1] if len(argv) > 1 else "install/python/Lib/site-packages"
    ok, message = verify(target)
    print(("[OK] " if ok else "[FAIL] ") + message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
