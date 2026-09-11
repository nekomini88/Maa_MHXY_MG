#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剑会「开始匹配」点击链路本地端到端验证（不需真机）。

做法：合成一张剑会弹窗截图（含诱饵标题「请及时开始匹配」+ 按钮「开始匹配」），
用 MaaFw 的 CustomController 冒充设备（screencap 返回合成图、click 记录坐标），
把仓库真实的 pipeline + 识别器跑起来，看框架最终点到哪。

判定：
  * 存在按钮场景 —— 点中的坐标必须落在「开始匹配」按钮矩形内；
  * 只有诱饵标题场景 —— 不允许出现任何点击（老版本会点标题）。

用法：
    /root/.venv-maacheck/bin/python tools/dev/jianhui_e2e_check.py
    /root/.venv-maacheck/bin/python tools/dev/jianhui_e2e_check.py \
        --recognition <某份识别器.py>   # 对比历史版本，复现旧 bug
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUNDLE = os.path.join(REPO, "assets", "resource", "base")
DEFAULT_RECOGNITION = os.path.join(
    REPO, "agent", "custom", "recognition", "jianhui_pipei.py"
)
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]


def _font(size: int):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    raise SystemExit("找不到 CJK 字体，无法生成测试截图")


def build_frame(width: int, height: int, with_button: bool = True):
    """合成剑会弹窗截图：返回 (BGR ndarray, 按钮矩形或 None)。"""
    img = Image.new("RGB", (width, height), (28, 34, 48))
    d = ImageDraw.Draw(img)
    body = _font(max(22, height // 34))
    title = _font(max(24, height // 30))

    # 弹窗底板
    d.rounded_rectangle(
        [int(width * 0.24), int(height * 0.14), int(width * 0.76), int(height * 0.72)],
        radius=18,
        fill=(46, 54, 74),
    )
    # 诱饵标题：「请及时开始匹配」（含关键词但结构上不该被点）
    d.text((int(width * 0.30), int(height * 0.20)), "请及时开始匹配", font=title, fill=(255, 236, 200))
    d.text((int(width * 0.30), int(height * 0.28)), "点击开始匹配进入对局", font=body, fill=(180, 190, 210))

    def _button(label: str, cx_ratio: float, label_color=(255, 255, 255), face=(228, 132, 44)):
        """按文字实际尺寸画按钮，返回 (x, y, w, h)。"""
        tw = int(d.textlength(label, font=title))
        th = int(title.size)
        b_h = int(th * 1.9)
        b_w = tw + max(24, th)
        b_x = int(width * cx_ratio)
        b_y = int(height * 0.56)
        d.rounded_rectangle([b_x, b_y, b_x + b_w, b_y + b_h], radius=10, fill=face)
        d.text((b_x + (b_w - tw) // 2, b_y + (b_h - th) // 2 - int(th * 0.12)), label, font=title, fill=label_color)
        return (b_x, b_y, b_w, b_h)

    # 「我再等等」（按需求不点）
    _button("我再等等", 0.28, label_color=(210, 216, 230), face=(70, 78, 98))

    button = None
    if with_button:
        button = _button("开始匹配", 0.58)

    return np.asarray(img)[:, :, ::-1].copy(), button


def _install_agent_server_stub():
    """stub 掉 maa.agent.agent_server。

    真机那侧这个模块跑在 agent 进程里（libMaaAgentServer.so），本地空跑时若
    真的 import 它，MaaFw 的 Library 会先加载 agent server 库，随后所有框架
    调用都会报 "Not implement this API"。这里只替换「注册用的装饰器」，
    被测识别器自身的 analyze 逻辑一行不改地原样执行。
    """
    import types

    if "maa.agent.agent_server" in sys.modules:
        return
    stub = types.ModuleType("maa.agent.agent_server")

    class _AgentServer:
        @staticmethod
        def custom_recognition(name):
            def deco(cls):
                cls._registered_name = name
                return cls

            return deco

    stub.AgentServer = _AgentServer
    sys.modules["maa.agent.agent_server"] = stub


def load_recognition(path: str):
    """按路径加载识别器模块，返回 (类, 注册名)。"""
    agent_dir = os.path.join(REPO, "agent")
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)
    _install_agent_server_stub()
    spec = importlib.util.spec_from_file_location("jh_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for value in vars(module).values():
        if isinstance(value, type) and hasattr(value, "analyze") and value.__module__ == "jh_probe":
            return value, getattr(value, "_registered_name", "jianhui_pipei")
    raise SystemExit(f"没能从 {path} 里找到识别器类")


def run_case(width, height, with_button, recognition_path, wait_s=25.0, verbose=False):
    import maa
    from maa.controller import CustomController
    from maa.define import LoggingLevelEnum
    from maa.resource import Resource
    from maa.tasker import Tasker

    if verbose:
        Tasker.set_stdout_level(LoggingLevelEnum.All)
        Tasker.set_debug_mode(True)

    results = {}

    class FakeDevice(CustomController):
        def __init__(self):
            self.clicks = []
            self._last_touch = None
            super().__init__()

        def connect(self):
            return True

        def request_uuid(self):
            return f"fake-{width}x{height}"

        def start_app(self, intent):
            return True

        def stop_app(self, intent):
            return True

        def screencap(self):
            results.setdefault("screencaps", 0)
            results["screencaps"] += 1
            return results["frame"]

        def click(self, x, y):
            self.clicks.append((int(x), int(y)))
            return True

        def touch_down(self, contact, x, y, pressure):
            # 框架的 Click 动作走的是 touch_down + touch_up（不是 click），
            # 所以这里按触点记录，touch_up 时落账一次点击。
            self._last_touch = (int(x), int(y))
            return True

        def touch_up(self, contact):
            if self._last_touch:
                self.clicks.append(self._last_touch)
                self._last_touch = None
            return True

        def touch_move(self, contact, x, y, pressure):
            return True

        def swipe(self, x1, y1, x2, y2, duration):
            return True

        def click_key(self, keycode):
            return True

        def input_text(self, text):
            return True

        def key_down(self, keycode):
            return True

        def key_up(self, keycode):
            return True

        def get_custom_info(self):
            return {"resolution": {"width": width, "height": height}}

    frame, button = build_frame(width, height, with_button)
    results["frame"] = frame
    results["button"] = button

    rec_cls, rec_name = load_recognition(recognition_path)
    res = Resource()
    res.post_bundle(BUNDLE).wait()
    res.register_custom_recognition(rec_name, rec_cls())

    ctrl = FakeDevice()
    tasker = Tasker()
    tasker.bind(res, ctrl)
    if not tasker.inited:
        raise SystemExit(f"tasker 初始化失败: {tasker.inited}")
    job = tasker.post_task("jianhui_pipei")
    time.sleep(wait_s)
    tasker.post_stop().wait()
    job.wait()

    return button, ctrl.clicks, results.get("screencaps", 0), frame.shape[:2]


def inside_button(button, x, y) -> bool:
    """点击坐标是否落在按钮矩形内。"""
    if not button:
        return False
    bx, by, bw, bh = button
    return bx <= x <= bx + bw and by <= y <= by + bh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recognition", default=DEFAULT_RECOGNITION)
    ap.add_argument("--wait", type=float, default=25.0)
    ap.add_argument("--verbose", action="store_true", help="打开框架 All 级日志，看动作执行细节")
    ap.add_argument("--only", type=int, default=None, help="只跑第 N 个场景（从 1 开始）")
    ap.add_argument("--json", action="store_true", help="末尾输出一行 RESULT_JSON: 供程序解析")
    args = ap.parse_args()

    label = os.path.relpath(args.recognition, REPO)
    if not args.json:
        print(f"被测识别器：{label}\n")
    failures = []
    case_results = []

    cases = [
        ("横屏 1280x720 · 含按钮", 1280, 720, True),
        ("竖屏 720x1280 · 含按钮", 720, 1280, True),
        ("横屏 1280x720 · 只有诱饵标题", 1280, 720, False),
    ]
    for idx, (name, w, h, with_button) in enumerate(cases, 1):
        if args.only and idx != args.only:
            continue
        button, clicks, shots, shape = run_case(
            w, h, with_button, args.recognition, args.wait, verbose=args.verbose
        )
        if not args.json:
            print(f"== {name} | 帧={shape[1]}x{shape[0]} 截图次数={shots} 点击次数={len(clicks)}")
        ok = True
        if with_button:
            hit = [c for c in clicks if inside_button(button, *c)]
            if not args.json:
                print(f"   按钮矩形={button} 命中按钮的点击={hit}")
            if not clicks:
                ok = False
                failures.append(f"{name}: 一次都没点击")
            elif not hit:
                ok = False
                failures.append(f"{name}: 点击未落在按钮上，实际={clicks[:4]}")
        else:
            if not args.json:
                print(f"   点击={clicks}")
            if clicks:
                ok = False
                failures.append(f"{name}: 只应识别到诱饵标题，却发生了点击 {clicks[:4]}")
        case_results.append(
            {
                "name": name,
                "ok": ok,
                "button": list(button) if button else None,
                "clicks": [list(c) for c in clicks],
                "screencaps": shots,
                "frame": [shape[1], shape[0]],
            }
        )
        if not args.json:
            print()

    if args.json:
        print(
            "RESULT_JSON:"
            + json.dumps(
                {"recognition": label, "ok": not failures, "failures": failures, "cases": case_results},
                ensure_ascii=False,
            )
        )
        return 1 if failures else 0

    if failures:
        print("❌ 未通过：")
        for f in failures:
            print("  -", f)
        return 1
    print("✅ 全部通过：按钮场景点中按钮，只有标题时不误点")
    return 0


if __name__ == "__main__":
    sys.exit(main())
