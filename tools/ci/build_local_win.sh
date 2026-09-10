#!/usr/bin/env bash
# 在 Linux 上打 Windows 包（与 CI 同流程，用于 Actions 不可用时本地出包）。
#
# 前置条件（与 CI 一致，缺哪个补哪个）：
#   deps/    MaaFramework win-x64 解压内容
#   MFA/     MFAAvalonia win-x64 解压内容
#   install/deps/  Windows 轮子（python3 -m pip download -r requirements.txt -d install/deps
#                  --only-binary=:all: --platform win_amd64 --python-version 312
#                  --implementation cp --abi cp312）
#
# 用法: bash tools/ci/build_local_win.sh [版本号]   默认 v0.1.2
set -euo pipefail

VERSION="${1:-v0.1.2}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "==> [1/7] 预装 Windows 轮子到 embed python"
rm -rf install/python/Lib
mkdir -p install/python/Lib/site-packages
python3 -m pip install -q --no-index --find-links install/deps \
    --platform win_amd64 --python-version 312 --implementation cp --abi cp312 \
    --target install/python/Lib/site-packages --only-binary=:all: \
    -r requirements.txt
rm -rf install/python/Lib/site-packages/bin
python3 tools/ci/verify_embed_deps.py install/python/Lib/site-packages

echo "==> [2/7] 补 pip 包体（Windows 端 python -m pip 可用，不执行 exe）"
PIP_WHL="$(python3 -c "import urllib.request,json; print(json.load(urllib.request.urlopen('https://pypi.org/pypi/pip/json'))['urls'][0]['url'])")"
curl -sL --retry 3 -o /tmp/pip.whl "$PIP_WHL"
python3 -c "import zipfile; zipfile.ZipFile('/tmp/pip.whl').extractall('install/python/Lib/site-packages')"

echo "==> [3/7] 运行 tools/install.py"
python3 ./tools/install.py "$VERSION" win-x64

echo "==> [4/7] 复制 registry 与 MFAAvalonia"
cp -rn tools/registry/. install/
if [ -d MFA ]; then
    cp -rn MFA/. install/
    [ -f install/MFAAvalonia.exe ] && mv -f install/MFAAvalonia.exe install/Maa_MHXY_MG.exe
fi
[ -f Maa_MHXY_MG.ico ] && cp Maa_MHXY_MG.ico install/logo.ico

echo "==> [5/7] 修正 child_exec 为 Windows 内嵌解释器"
python3 - <<'PY'
import json
p = 'install/interface.json'
d = json.load(open(p, encoding='utf-8'))
d['agent']['child_exec'] = './python/python.exe'
d['agent']['child_args'] = ['./agent/main.py', '-u']
json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=4)
print('child_exec:', d['agent']['child_exec'], '| tasks:', len(d['task']))
PY

echo "==> [6/7] 打包（自动排除 install/deps 离线轮子仓库）"
python3 tools/ci/package.py install "Maa_MHXY_MG-win-x86_64-MFAA.zip"

echo "==> [7/7] 验包"
python3 tools/ci/verify_package.py "Maa_MHXY_MG-win-x86_64-MFAA.zip"

echo "==> 完成: $ROOT/Maa_MHXY_MG-win-x86_64-MFAA.zip"
