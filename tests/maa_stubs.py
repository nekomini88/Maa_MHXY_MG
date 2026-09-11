# -*- coding: utf-8 -*-
"""识别器单测共用的桩件：假 maa / utils、假设备与假识别结果。

擂台 / 竞技场 / 剑会三个「自动点开始匹配」识别器的单测都跑在这里定义的
假对象上——服务器上通常没装 MaaFramework，桩掉之后识别器逻辑仍按原样执行。

两个容易踩的点：

* ``utils.__path__`` 必须指向**真实**的 ``agent/utils``：``utils.ocr_button``
  是被测逻辑（锚定关键词 + 候选复查）的一部分，用桩就等于没测。
* ``FakeReco.box`` 特意保持「框架 best」的语义（默认取第一个候选的框），
  这样「识别器会不会退回框架 best」这件事才测得出来。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = REPO_ROOT / "agent"
UTILS_DIR = AGENT_DIR / "utils"


class FakeTime:
    """可推进的假时钟：sleep 直接推进 now，避免测试真的等。"""

    def __init__(self, start=1000.0):
        self.now = start

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(float(seconds), 0.0)


class FakeJob:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def wait(self):
        return self

    def get(self):
        if self.error:
            raise self.error
        return self.value


class FakeRect:
    """maafw 绑定里的 ``Rect`` dataclass（x, y, w, h）——不是 tuple，别按下标取。"""

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


class FakeOcrItem:
    def __init__(self, text, box=(10, 10, 100, 20), score=0.9):
        self.text = text
        self.box = box
        self.score = score


class FakeReco:
    """假的 ``RecognitionDetail``（字段名与 maafw 绑定一致）。"""

    def __init__(self, items=None, raw_only=False):
        items = items or []
        self.all_results = [] if raw_only else list(items)
        self.filtered_results = [] if raw_only else list(items)
        self.best_result = None if raw_only or not items else items[0]
        self.hit = bool(items)
        # 框架的 best：默认取第一个候选（旧版行为里正是被标题抢走的那个）。
        self.box = items[0].box if items else None
        if raw_only:
            # 老版本绑定：只有 raw_detail 里的 JSON，没有解析好的 dataclass。
            self.raw_detail = {
                "filtered": [
                    {"text": it.text, "box": as_box_list(it.box), "score": it.score}
                    for it in items
                ],
                "all": [],
                "best": None,
            }
        else:
            self.raw_detail = {}


def as_box_list(box):
    if isinstance(box, FakeRect):
        return [box.x, box.y, box.w, box.h]
    return list(box)


class _FakeImage:
    """只提供 shape 的假帧（构造比例 ROI 用）。"""

    shape = (607, 1080, 3)


class FakeController:
    """假控制器：截图返回假帧，点击按坐标记账。"""

    def __init__(self):
        self.error = None
        self.count = 0
        self.clicks = []
        self.shape_image = _FakeImage()

    def post_screencap(self):
        self.count += 1
        if self.error is not None:
            return FakeJob(error=self.error)
        return FakeJob(value=self.shape_image)

    def post_click(self, x, y):
        self.clicks.append((int(x), int(y)))
        return FakeJob(value=True)


class FakeTasker:
    def __init__(self):
        self.controller = FakeController()
        self.stopping = False


class FakeContext:
    def __init__(self, ocr_items=None, reco_error=None, raw_only=False):
        self.tasker = FakeTasker()
        self.ocr_items = ocr_items
        self.reco_error = reco_error
        self.raw_only = raw_only
        self.recognition_calls = 0
        self.last_override = None

    def run_recognition(self, name, image, pipeline_override=None):
        self.recognition_calls += 1
        self.last_override = pipeline_override
        if self.reco_error is not None:
            raise self.reco_error
        return FakeReco(mk_ocr_items(self.ocr_items), raw_only=self.raw_only)


def mk_ocr_items(raw):
    """把测试里的简写（str / (text, box) / (text, box, score)）统一成候选对象。"""
    items = []
    for it in raw or []:
        if isinstance(it, FakeOcrItem):
            items.append(it)
        elif isinstance(it, str):
            items.append(FakeOcrItem(it))
        else:
            items.append(FakeOcrItem(*it))
    return items


class AnalyzeArgStub:
    custom_recognition_param = "{}"


class _Result:
    def __init__(self, box=None, detail=""):
        self.box = box
        self.detail = detail


def install_stubs():
    """注册假 maa / utils 模块；同进程里已被别的测试装过就直接复用。"""
    utils = sys.modules.get("utils")
    if utils is not None and hasattr(utils, "logger") and hasattr(utils, "send_message"):
        return utils

    maa = types.ModuleType("maa")
    maa.__path__ = []
    maa_agent = types.ModuleType("maa.agent")
    maa_agent.__path__ = []
    agent_server = types.ModuleType("maa.agent.agent_server")

    class _AgentServer:
        @staticmethod
        def custom_recognition(name):
            def deco(cls):
                cls.__recognition_name__ = name
                return cls

            return deco

    agent_server.AgentServer = _AgentServer

    custom_recognition = types.ModuleType("maa.custom_recognition")

    class _CustomRecognition:
        AnalyzeResult = _Result
        AnalyzeArg = AnalyzeArgStub

        def analyze(self, context, argv):  # pragma: no cover - 抽象方法
            raise NotImplementedError

    custom_recognition.CustomRecognition = _CustomRecognition
    context_mod = types.ModuleType("maa.context")
    context_mod.Context = object

    for name, mod in (
        ("maa", maa),
        ("maa.agent", maa_agent),
        ("maa.agent.agent_server", agent_server),
        ("maa.custom_recognition", custom_recognition),
        ("maa.context", context_mod),
    ):
        sys.modules[name] = mod

    class _Logger:
        def info(self, msg):
            return None

        def warning(self, msg):
            return None

    utils = types.ModuleType("utils")
    # 关键：路径指向真实 agent/utils，让 utils.ocr_button 走真实现而非桩。
    utils.__path__ = [str(UTILS_DIR)]
    utils.logger = _Logger()
    utils.send_message = lambda *a, **k: True
    sys.modules["utils"] = utils
    return utils


def make_arg(**params):
    arg = AnalyzeArgStub()
    arg.custom_recognition_param = json.dumps(params, ensure_ascii=False)
    return arg


def load_recognizer(filename: str):
    """加载某个识别器模块，并把它的 ``time`` 换成假时钟。

    同一进程里重复调用返回同一个模块对象，识别器类上的冷却状态才能跨用例
    被检查（这正是「状态挂类上」那条规则的验证方式）。
    """
    install_stubs()
    stem = Path(filename).stem
    module_name = f"{stem}_under_test"
    if module_name in sys.modules:
        module = sys.modules[module_name]
    else:
        path = AGENT_DIR / "custom" / "recognition" / filename
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    module.time = FakeTime()
    return module


PIPELINE_DIR = REPO_ROOT / "assets" / "resource" / "base" / "pipeline"


def load_pipeline(filename: str) -> dict:
    return json.loads((PIPELINE_DIR / filename).read_text(encoding="utf-8"))


def all_pipeline_nodes() -> set:
    """所有 pipeline 文件里的节点名合集。

    节点可以跨文件引用（例如 ``panduan_zhujiemian`` 在别的文件里），
    只查本文件会把正常的跨文件跳转误判成悬空引用。
    """
    names = set()
    for path in PIPELINE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            names.update(data)
    return names


def node_refs(pipe: dict):
    """产出 ``(节点名, 字段, 被引用的节点名)``，去掉 JumpBack/Anchor 前缀。"""
    for name, node in pipe.items():
        if not isinstance(node, dict):
            continue
        for key in ("next", "on_error", "interrupt"):
            refs = node.get(key) or []
            if not isinstance(refs, list):
                continue
            for ref in refs:
                if isinstance(ref, str):
                    target = ref.replace("[JumpBack]", "").replace("[Anchor]", "")
                    yield name, key, target
