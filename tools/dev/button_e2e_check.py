#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三个「自动点开始匹配」任务的点击链路本地端到端验证（不需真机）。

覆盖擂台 / 竞技场 / 剑会。做法都一样：按任务合成一张面板截图（含诱饵文字 +
真按钮），用 MaaFw 的 CustomController 冒充设备（screencap 返回合成图、
click 记录坐标），把仓库真实的 pipeline + 识别器跑起来，看框架最终点到哪。

判定：
  * 含按钮场景 —— 每一次点击都必须落在「开始匹配」按钮矩形内；
  * 只有诱饵场景 —— 不允许出现任何点击（旧关键词/固定 ROI 的版本会点诱饵）。

用法：
    /root/.venv-maacheck/bin/python tools/dev/button_e2e_check.py
    /root/.venv-maacheck/bin/python tools/dev/button_e2e_check.py --task leitai
    /root/.venv-maacheck/bin/python tools/dev/button_e2e_check.py --task jianhui \
        --recognition <某份历史识别器.py>     # 复现旧 bug / 做修前修后对比
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


def _canvas(width: int, height: int):
    """画布 + 随分辨率缩放的字体（竖屏也放得下长句）。"""
    img = Image.new("RGB", (width, height), (28, 34, 48))
    draw = ImageDraw.Draw(img)
    body_size = max(18, min(height // 34, width // 30))
    return img, draw, _font(body_size), _font(int(body_size * 1.25))


def _button(draw, size, label, cx_ratio, cy_ratio, font, face, label_color=(255, 255, 255)):
    """按文字实际尺寸画一个按钮，返回 (x, y, w, h)——就是 OCR 该读到的那个框。"""
    width, height = size
    text_w = int(draw.textlength(label, font=font))
    text_h = int(font.size)
    box_h = int(text_h * 1.9)
    box_w = text_w + max(24, text_h)
    x = int(width * cx_ratio)
    y = int(height * cy_ratio)
    draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=10, fill=face)
    draw.text(
        (x + (box_w - text_w) // 2, y + (box_h - text_h) // 2 - int(text_h * 0.12)),
        label,
        font=font,
        fill=label_color,
    )
    return (x, y, box_w, box_h)


def build_jianhui_frame(width, height, with_button=True):
    """剑会弹窗：诱饵标题「请及时开始匹配」+ 说明行 + 按钮「开始匹配」。"""
    img, d, body, title = _canvas(width, height)
    d.rounded_rectangle(
        [int(width * 0.24), int(height * 0.14), int(width * 0.76), int(height * 0.72)],
        radius=18,
        fill=(46, 54, 74),
    )
    d.text((int(width * 0.30), int(height * 0.20)), "请及时开始匹配", font=title, fill=(255, 236, 200))
    d.text((int(width * 0.30), int(height * 0.28)), "点击开始匹配进入对局", font=body, fill=(180, 190, 210))
    _button(d, (width, height), "我再等等", 0.28, 0.56, title, (70, 78, 98), (210, 216, 230))
    button = None
    if with_button:
        button = _button(d, (width, height), "开始匹配", 0.58, 0.56, title, (228, 132, 44))
    return np.asarray(img)[:, :, ::-1].copy(), button


def build_leitai_frame(width, height, with_button=True):
    """擂台面板：说明行「点击开始匹配…」在按钮左边（旧版取最左命中框就会点它），
    外加游戏里真实存在、含「匹配」但不含「开始匹配」的余票提示，任何情况都不该被点。"""
    img, d, body, title = _canvas(width, height)
    d.rounded_rectangle(
        [int(width * 0.18), int(height * 0.10), int(width * 0.86), int(height * 0.94)],
        radius=18,
        fill=(40, 48, 66),
    )
    d.text((int(width * 0.24), int(height * 0.16)), "擂台大战", font=title, fill=(255, 236, 200))
    d.text((int(width * 0.50), int(height * 0.70)), "点击开始匹配，队伍满5人开战", font=body, fill=(180, 190, 210))
    d.text(
        (int(width * 0.20), int(height * 0.88)),
        "队伍组满5人方可开始擂台乱斗匹配",
        font=body,
        fill=(150, 160, 180),
    )
    button = None
    if with_button:
        button = _button(d, (width, height), "开始匹配", 0.62, 0.78, title, (228, 132, 44))
    return np.asarray(img)[:, :, ::-1].copy(), button


def build_jingjichang_frame(width, height, with_button=True):
    """竞技场面板：右下角一栏，说明文字「开始匹配后自动进入对局」压在按钮上方
    （旧 ROI 正好把这行和按钮框在一起）。"""
    img, d, body, title = _canvas(width, height)
    d.rounded_rectangle(
        [int(width * 0.10), int(height * 0.10), int(width * 0.92), int(height * 0.94)],
        radius=18,
        fill=(40, 48, 66),
    )
    d.text((int(width * 0.20), int(height * 0.16)), "竞技场", font=title, fill=(255, 236, 200))
    d.text(
        (int(width * 0.78), int(height * 0.76)),
        "开始匹配后自动进入对局",
        font=body,
        fill=(180, 190, 210),
    )
    button = None
    if with_button:
        button = _button(d, (width, height), "开始匹配", 0.82, 0.83, title, (228, 132, 44))
    return np.asarray(img)[:, :, ::-1].copy(), button


TASKS = {
    "jianhui": {
        "label": "剑会",
        "entry": "jianhui_pipei",
        "recognition": "agent/custom/recognition/jianhui_pipei.py",
        "build": build_jianhui_frame,
        "override": None,
        "decoy": "弹窗标题「请及时开始匹配」+ 说明行",
    },
    "leitai": {
        "label": "擂台匹配",
        "entry": "leitai_pipei",
        "recognition": "agent/custom/recognition/leitai_pipei.py",
        "build": build_leitai_frame,
        "override": None,
        "decoy": "说明行「点击开始匹配，队伍满5人开战」+ 余票提示",
    },
    "jingjichang": {
        "label": "竞技场",
        "entry": "jingjichang",
        "recognition": "agent/custom/recognition/jingjichang.py",
        "build": build_jingjichang_frame,
        # 空跑不该真等 10 场 × 5 秒：把场次/节流调小，只验点击落点。
        "override": {
            "竞技场-开始挂机": {
                "custom_recognition_param": {
                    "jjc_enabled": True,
                    "jjc_max_clicks": 2,
                    "jjc_click_delay": 0.5,
                    "jjc_interval": 0.2,
                    "jjc_max_wait": 2.0,
                }
            }
        },
        "decoy": "说明行「开始匹配后自动进入对局」",
    },
}

CASES = [
    ("横屏 1280x720 · 含按钮", 1280, 720, True),
    ("竖屏 720x1280 · 含按钮", 720, 1280, True),
    ("横屏 1280x720 · 只有诱饵文字", 1280, 720, False),
]


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
    spec = importlib.util.spec_from_file_location("btn_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for value in vars(module).values():
        if isinstance(value, type) and hasattr(value, "analyze") and value.__module__ == "btn_probe":
            return value, getattr(value, "_registered_name", None)
    raise SystemExit(f"没能从 {path} 里找到识别器类")


def run_case(task, width, height, with_button, recognition_path, wait_s=25.0, verbose=False):
    import maa  # noqa: F401
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
            results["screencaps"] = results.get("screencaps", 0) + 1
            return results["frame"]

        def click(self, x, y):
            # 识别器内部的 post_click 走这里。
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

    frame, button = TASKS[task]["build"](width, height, with_button)
    results["frame"] = frame

    rec_cls, rec_name = load_recognition(recognition_path)
    if not rec_name:
        raise SystemExit(f"{recognition_path} 里没有找到注册名（装饰器没被执行？）")

    res = Resource()
    res.post_bundle(BUNDLE).wait()
    res.register_custom_recognition(rec_name, rec_cls())

    ctrl = FakeDevice()
    tasker = Tasker()
    tasker.bind(res, ctrl)
    if not tasker.inited:
        raise SystemExit("tasker 初始化失败")
    job = tasker.post_task(TASKS[task]["entry"], TASKS[task]["override"] or {})
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


def run_task(task, args):
    spec = TASKS[task]
    recognition = args.recognition or os.path.join(REPO, spec["recognition"])
    label = os.path.relpath(recognition, REPO)
    if not args.json:
        print(f"===== {spec['label']}（{task}） 被测识别器：{label}")
        print(f"      诱饵：{spec['decoy']}\n")

    failures = []
    case_results = []
    for idx, (name, w, h, with_button) in enumerate(CASES, 1):
        if args.only and idx != args.only:
            continue
        button, clicks, shots, shape = run_case(task, w, h, with_button, recognition, args.wait, args.verbose)
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
            elif len(hit) != len(clicks):
                ok = False
                failures.append(f"{name}: 有点击没落在按钮上，实际={clicks[:4]}")
        else:
            if not args.json:
                print(f"   点击={clicks}")
            if clicks:
                ok = False
                failures.append(f"{name}: 只应识别到诱饵文字，却发生了点击 {clicks[:4]}")
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

    result = {
        "task": task,
        "label": spec["label"],
        "recognition": label,
        "ok": not failures,
        "failures": failures,
        "cases": case_results,
    }
    if args.json:
        print("RESULT_JSON:" + json.dumps(result, ensure_ascii=False))
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="all", help="jianhui / leitai / jingjichang / all（逗号分隔）")
    ap.add_argument("--recognition", default=None, help="只跑单个任务时用它替换识别器路径，做修前修后对比")
    ap.add_argument("--wait", type=float, default=25.0)
    ap.add_argument("--verbose", action="store_true", help="打开框架 All 级日志，看动作执行细节")
    ap.add_argument("--only", type=int, default=None, help="只跑第 N 个场景（从 1 开始）")
    ap.add_argument("--json", action="store_true", help="每个任务输出一行 RESULT_JSON: 供程序解析")
    args = ap.parse_args()

    tasks = list(TASKS) if args.task == "all" else [t.strip() for t in args.task.split(",") if t.strip()]
    for task in tasks:
        if task not in TASKS:
            raise SystemExit(f"未知任务 {task}，可选：{', '.join(TASKS)}")
    if args.recognition and len(tasks) != 1:
        raise SystemExit("--recognition 只能配合单个 --task 使用（各任务识别器不同）")

    results = [run_task(task, args) for task in tasks]
    failures = [f"{r['label']} → {f}" for r in results for f in r["failures"]]

    if args.json:
        return 1 if failures else 0

    if failures:
        print("❌ 未通过：")
        for f in failures:
            print("  -", f)
        return 1
    print(f"✅ {len(results)} 个任务全部通过：含按钮场景点中按钮，只有诱饵文字时零点击")
    return 0


if __name__ == "__main__":
    sys.exit(main())
