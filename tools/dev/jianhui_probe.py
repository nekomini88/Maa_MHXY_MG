# -*- coding: utf-8 -*-
"""探针：直接问框架「带诱饵标题时 OCR 到底选哪个框」。

复用 button_e2e_check 的合成截图与假设备，注册一个只 dump 不点击的识别器，
分别用【旧关键词 非锚定】和【新关键词 锚定】各跑一次 OCR，打印：
  * RecognitionDetail.box（框架最终选中的框，pipeline Click 用的就是它）
  * filtered_results / best_result / all_results 的文本与框
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

import importlib.util

REPO = "/root/Maa_MHXY_MG"
spec = importlib.util.spec_from_file_location(
    "bte2e", os.path.join(REPO, "tools", "dev", "button_e2e_check.py")
)
bte2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bte2e)
bte2e._install_agent_server_stub()

from maa.controller import CustomController
from maa.custom_recognition import CustomRecognition
from maa.resource import Resource
from maa.tasker import Tasker

W, H = 1280, 720
FRAME, BUTTON = bte2e.build_jianhui_frame(W, H, True)

_arg_image = None
for _i, _a in enumerate(sys.argv):
    if _a == "--image" and _i + 1 < len(sys.argv):
        _arg_image = sys.argv[_i + 1]
if _arg_image:
    from PIL import Image as _Image
    import numpy as _np

    _rgb = _np.asarray(_Image.open(_arg_image).convert("RGB"))
    FRAME = _rgb[:, :, ::-1].copy()
    H, W = FRAME.shape[:2]
    BUTTON = None
    print(f"用真实截图：{_arg_image} ({W}x{H})")


def dump(tag, reco):
    print(f"\n--- {tag} hit={reco.hit} box={reco.box} best={reco.best_result}")
    for name in ("filtered_results", "all_results"):
        items = getattr(reco, name, None) or []
        print(f"    {name}: {[(getattr(i,'text',None), getattr(i,'box',None), round(getattr(i,'score',0),3)) for i in items]}")
    print(f"    raw_detail keys={list(getattr(reco, 'raw_detail', {}).keys()) if isinstance(getattr(reco,'raw_detail',None), dict) else type(getattr(reco,'raw_detail',None))}")


class Probe(CustomRecognition):
    def analyze(self, context, argv):
        image = context.tasker.controller.post_screencap().wait().get()
        print(f"\n截图 shape={image.shape}")
        for tag, expected in (
            ("旧关键词（非锚定）", ["开始匹配", "开始匹", "始匹配"]),
            ("新关键词（锚定）", [r"^\s*开始匹配\s*$"]),
        ):
            reco = context.run_recognition(
                "probe-ocr",
                image,
                pipeline_override={
                    "probe-ocr": {
                        "recognition": "OCR",
                        "roi": [0, 0, 0, 0],
                        "expected": expected,
                        "threshold": 0.6,
                    }
                },
            )
            dump(tag, reco)
        if BUTTON:
            print(f"\n按钮矩形={BUTTON}（中心=({BUTTON[0]+BUTTON[2]//2},{BUTTON[1]+BUTTON[3]//2})）")
        else:
            print("\n（真实截图，无已知按钮矩形；看上面被选中的框落在哪段文字上）")
        return CustomRecognition.AnalyzeResult(box=None, detail="probe done")


class FakeDevice(CustomController):
    def connect(self): return True
    def request_uuid(self): return "probe"
    def start_app(self, intent): return True
    def stop_app(self, intent): return True
    def screencap(self): return FRAME
    def click(self, x, y):
        print(f"    [FakeDevice] click({x},{y})")
        return True
    def swipe(self, *a): return True
    def touch_down(self, *a): return True
    def touch_move(self, *a): return True
    def touch_up(self, *a): return True
    def click_key(self, k): return True
    def input_text(self, t): return True
    def key_down(self, k): return True
    def key_up(self, k): return True
    def get_custom_info(self): return {"resolution": {"width": W, "height": H}}


res = Resource()
res.post_bundle(os.path.join(REPO, "assets", "resource", "base")).wait()
res.register_custom_recognition("jianhui_pipei", Probe())
tasker = Tasker()
tasker.bind(res, FakeDevice())
print("tasker inited:", tasker.inited)
tasker.post_task("jianhui_pipei")
time.sleep(12)
tasker.post_stop().wait()
print("\n探针结束")
