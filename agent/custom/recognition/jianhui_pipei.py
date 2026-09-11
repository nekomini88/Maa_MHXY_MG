# -*- coding: utf-8 -*-
"""剑会匹配按钮识别 + 点击（CustomRecognition 供 pipeline 调用）。

自定义识别器 ``jianhui_pipei``：监测剑会房间弹窗里的「开始匹配」**按钮**，
出现即返回按钮命中框，由 pipeline 节点执行 Click 点下。

为什么不按比例 ROI + 非锚定关键词（v0.1.8 及之前）
--------------------------------------------------
1. MaaFramework 的 OCR ``expected`` 是 **boost::regex_search（子串匹配）**，
   写成 ``"开始匹配"`` 时，弹窗标题「**请及时开始匹配**」同样命中；而
   ``cherry_pick()`` 默认按 ``order_by=Horizontal`` 取最靠左的结果，
   标题在按钮左侧 → 框架选中的是标题框，Click 点到了文字上，
   按钮自然“没被点到”。（参照 Maafw v5.12.2 ``Vision/OCRer.cpp``：
   ``filter_by_required`` 用 ``regex_search``、``sort_`` 默认 Horizontal。）
2. 固定比例 ROI（``[0.27w, 0.18h, 0.48w, 0.50h]``）是按某一张横屏截图
   量的，换分辨率/竖屏就可能整块错开，直接把按钮排除在扫描区外。

现在的做法
----------
* ``expected`` 默认用**锚定**正则 ``^\\s*开始匹配\\s*$``，标题「请及时开始匹配」
  结构上不可能再命中；
* 拿到 OCR 结果后**再过一遍候选框**：只接受整段文本（去空白/标点后）
  等于「开始匹配」的框，并排除含「请及时/等等/取消/关闭/等待」的文本
  （黑名单 ``BLOCKLIST``）。框架 JSON 结构变化时退回 ``reco.box``，
  此时锚定正则已保证候选集里没有标题；
* 默认 **ROI = 全屏**，与分辨率/横竖屏无关；要缩小扫描范围可显式传
  ``jianhui_roi``（绝对像素）或 ``jianhui_roi_ratio``（比例）。

常驻语义（关键设计）：本识别器**不负责结束任务**。单轮最多守候
``jianhui_max_wait`` 秒，等不到按钮就返回未命中，由 pipeline 节点的
``on_error`` 自循环回到本节点继续等；命中并点击后同样由 ``next`` 自循环
回到本节点，等下一次弹窗。所以「点一次就跑完退出」在这里是故障而不是
期望行为——剑会弹窗会反复出现（队友取消、匹配失败重试、打完一局回到
房间），必须一直守着。任务的结束只由用户手动停止触发，停止请求会在
下个轮询点立刻生效。

配置（pipeline 节点 custom_recognition_param 传入）：
    jianhui_enabled:          是否启用（默认 true）
    jianhui_expected:         OCR 关键词（默认 ["^\\s*开始匹配\\s*$"]，锚定正则）
    jianhui_roi:              自定义 ROI [x,y,w,h] 绝对像素（默认全屏 [0,0,0,0]）
    jianhui_roi_ratio:        按比例算 ROI [x,y,w,h]，显式给出才生效
    jianhui_threshold:        OCR 置信度阈值（默认 0.6）
    jianhui_max_wait:         单轮 analyze 最长等待秒数（默认 300）
    jianhui_interval:         轮询截图间隔秒（默认 1.0）
    jianhui_click_cooldown:   两次命中之间的最小间隔秒（默认 2.0，防同一弹窗连点）
    jianhui_notify:           命中是否发通知（默认 false，常规匹配不打扰）
"""

import json
import re
import time

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition
from maa.context import Context

from utils import logger
from utils import send_message

# 锚定正则：只有整段文本就是「开始匹配」才算数。
# 不是锚定的话，弹窗标题「请及时开始匹配」会被 regex_search 当成命中。
ANCHORED_EXACT = r"^\s*开始匹配\s*$"
DEFAULT_EXPECTED = [ANCHORED_EXACT]
DEFAULT_ROI = [0, 0, 0, 0]  # 全屏：不依赖分辨率与横竖屏
EXACT_TEXT = "开始匹配"
# 这些文本里也含「开始匹配」或长得很像，但都不是按钮，命中一律丢弃。
BLOCKLIST = ("请及时", "等等", "取消", "关闭", "等待")
DEFAULT_THRESHOLD = 0.6
DEFAULT_MAX_WAIT = 300
DEFAULT_INTERVAL = 1.0
DEFAULT_CLICK_COOLDOWN = 2.0


def _tasker_stopping(context: Context) -> bool:
    """任务是否已被请求停止。

    只看 ``tasker.stopping``：``tasker.running`` 在部分版本里会被识别回调
    自身改写，用它判断会让正常轮询误以为应该收工。
    """
    return bool(getattr(getattr(context, "tasker", None), "stopping", False))


def _rect_to_box(rect) -> list | None:
    """把各种形态的框统一成 [x, y, w, h]。

    绑定版本之间不一致：``RecognitionDetail.box`` 是 ``Rect(x, y, w, h)``，
    候选明细是同一套 dataclass，而 ``raw_detail`` 里的 JSON 是 list/dict。
    """
    if rect is None:
        return None
    if isinstance(rect, str):
        parts = [p for p in rect.replace(";", ",").split(",") if p.strip()]
        if len(parts) != 4:
            return None
        try:
            return [int(float(p)) for p in parts]
        except ValueError:
            return None
    if isinstance(rect, (list, tuple)):
        values = list(rect)
    elif isinstance(rect, dict):
        values = [
            rect.get("x"),
            rect.get("y"),
            rect.get("w", rect.get("width")),
            rect.get("h", rect.get("height")),
        ]
    else:
        values = [
            getattr(rect, "x", None),
            getattr(rect, "y", None),
            getattr(rect, "w", getattr(rect, "width", None)),
            getattr(rect, "h", getattr(rect, "height", None)),
        ]
    if len(values) != 4 or any(v is None for v in values):
        return None
    try:
        return [int(float(str(v))) for v in values]
    except (TypeError, ValueError):
        return None


def _field(cand, name, default=None):
    """候选字段读取：同时兼容 dataclass 与 raw JSON dict。"""
    if isinstance(cand, dict):
        return cand.get(name, default)
    return getattr(cand, name, default)


def _candidates(reco) -> list:
    """取 OCR 候选框列表（优先过滤后的 filtered_results，其次 all_results）。

    绑定自带解析好的 dataclass；老版本没有这两个属性时退回 raw_detail JSON。
    """
    for attr in ("filtered_results", "all_results"):
        items = getattr(reco, attr, None)
        if isinstance(items, (list, tuple)):
            picked = [c for c in items if _rect_to_box(_field(c, "box"))]
            if picked:
                return picked

    raw = getattr(reco, "raw_detail", None)
    if isinstance(raw, dict):
        for key in ("filtered", "all"):
            items = raw.get(key)
            if isinstance(items, list):
                picked = [c for c in items if isinstance(c, dict) and _rect_to_box(c.get("box"))]
                if picked:
                    return picked
    return []


def _normalize_text(text: str) -> str:
    """去掉空白与标点，只留文字本身。

    OCR 常把按钮上的文字读成「开始匹配」「开始 匹配」「开始匹配·」这类形态，
    去掉空白/标点后再比，既不误收标题，也不漏掉按钮。
    """
    return re.sub(r"[\s\u3000\W_]+", "", text, flags=re.UNICODE)


def pick_button_box(reco) -> tuple[list | None, str]:
    """从识别结果里挑出「开始匹配」按钮框，返回 (box, 说明)。

    判据是**整段文本**（去空白标点后）等于「开始匹配」：
    标题「请及时开始匹配」、说明行「点击开始匹配进入对局」都不满足，
    因此不会被当按钮点掉。含黑名单字样的文本一律排除。
    同一档内取 OCR 得分最高的，再取面积最大的。
    """
    best = None
    best_key = None
    seen_texts = []
    for cand in _candidates(reco):
        text = str(_field(cand, "text", "") or "").strip()
        if text:
            seen_texts.append(text)
        if not text or any(bad in text for bad in BLOCKLIST):
            continue
        if _normalize_text(text) != EXACT_TEXT:
            continue
        box = _rect_to_box(_field(cand, "box"))
        if not box:
            continue
        try:
            score = float(_field(cand, "score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        key = (score, box[2] * box[3])
        if best_key is None or key > best_key:
            best_key = key
            best = (box, text, score)

    if best is not None:
        box, text, score = best
        return box, f"命中文本「{text}」(score={score:.2f})"

    if seen_texts:
        # 有候选但一个都不是按钮（比如弹窗还在动画中）：不点，等下一轮。
        return None, "候选文本无一是按钮：" + " / ".join(seen_texts[:3])

    # 完全没有候选明细：绑定结构与预期不符时的兜底。此时框架侧已用锚定正则
    # 过滤过，box 不会落在标题上。
    box = _rect_to_box(getattr(reco, "box", None))
    if box:
        return box, "兜底：框架未提供候选明细"
    return None, "无候选框"


@AgentServer.custom_recognition("jianhui_pipei")
class JianhuiPipei(CustomRecognition):
    """识别「开始匹配」按钮，命中返回按钮框供 pipeline 点击。"""

    _ROC_NAME = "jianhui-pipei-ocr"
    _LAST_NOTIFY_TS = 0.0
    _LAST_HIT_TS = 0.0

    @staticmethod
    def _ratio_roi(image, ratio) -> list:
        h, w = image.shape[:2]
        rx, ry, rw, rh = ratio
        return [int(w * rx), int(h * ry), int(w * rw), int(h * rh)]

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult:
        raw = getattr(argv, "custom_recognition_param", None) or "{}"
        if not isinstance(raw, str):
            raw = "{}"
        try:
            param = json.loads(raw)
        except Exception:
            param = {}
        if not isinstance(param, dict):
            param = {}

        enabled = param.get("jianhui_enabled", True)
        if not enabled:
            return CustomRecognition.AnalyzeResult(box=None, detail="剑会匹配已禁用")

        expected = param.get("jianhui_expected", DEFAULT_EXPECTED)
        threshold = float(param.get("jianhui_threshold", DEFAULT_THRESHOLD))
        max_wait = float(param.get("jianhui_max_wait", DEFAULT_MAX_WAIT))
        interval = float(param.get("jianhui_interval", DEFAULT_INTERVAL))
        click_cooldown = float(
            param.get("jianhui_click_cooldown", DEFAULT_CLICK_COOLDOWN)
        )
        notify = bool(param.get("jianhui_notify", False))
        roi = param.get("jianhui_roi") or DEFAULT_ROI

        # 冷却/节流状态必须落在类上：analyze 每次都可能是新实例，
        # 写到 self 上会随实例一起丢掉（连点与重复通知都拦不住）。
        cls = type(self)

        ratio = param.get("jianhui_roi_ratio")
        need_capture_for_roi = (
            isinstance(ratio, (list, tuple))
            and len(ratio) == 4
            and not param.get("jianhui_roi")
        )

        logger.info(
            f"[jianhui_pipei] 开始守候「开始匹配」按钮，ROI={roi}，"
            f"关键词={expected}，最多等待{max_wait}s。"
        )

        deadline = time.time() + max_wait
        while time.time() < deadline:
            if _tasker_stopping(context):
                logger.info("[jianhui_pipei] 收到停止请求，立即结束本轮守候。")
                return CustomRecognition.AnalyzeResult(box=None, detail="任务已请求停止")

            try:
                image = context.tasker.controller.post_screencap().wait().get()
                if image is None:
                    raise RuntimeError("post_screencap 返回 None")
            except Exception as e:
                logger.warning(f"[jianhui_pipei] 截图失败（{e}），重试中...")
                time.sleep(interval)
                continue

            if need_capture_for_roi:
                roi = self._ratio_roi(image, ratio)

            try:
                reco = context.run_recognition(
                    self._ROC_NAME,
                    image,
                    pipeline_override={
                        self._ROC_NAME: {
                            "recognition": "OCR",
                            "roi": roi,
                            "expected": expected,
                            "threshold": threshold,
                        }
                    },
                )
            except Exception as e:
                logger.warning(f"[jianhui_pipei] OCR 调用异常（{e}），重试中...")
                time.sleep(interval)
                continue

            box, why = pick_button_box(reco) if reco and reco.hit else (None, "未命中")
            if box:
                # 同一弹窗可能因动画/网络延迟在屏幕上多停留一会儿，
                # 命中后按冷却间隔节流，避免对着同一个按钮连点。
                gap = time.time() - cls._LAST_HIT_TS
                if 0 <= gap < click_cooldown:
                    time.sleep(click_cooldown - gap)
                    if _tasker_stopping(context):
                        logger.info("[jianhui_pipei] 冷却期间收到停止请求，结束本轮守候。")
                        return CustomRecognition.AnalyzeResult(
                            box=None, detail="任务已请求停止"
                        )
                cls._LAST_HIT_TS = time.time()
                cx, cy = box[0] + box[2] // 2, box[1] + box[3] // 2
                logger.info(
                    f"[jianhui_pipei] ✅ 命中「开始匹配」按钮 {why}，"
                    f"框={box}（中心=({cx},{cy})，框架在框内取随机点），交给 pipeline Click。"
                )
                if notify and time.time() - cls._LAST_NOTIFY_TS >= 300:
                    try:
                        if send_message("剑会匹配", "已找到开始匹配按钮，准备点击。"):
                            cls._LAST_NOTIFY_TS = time.time()
                    except Exception as e:
                        logger.warning(f"[jianhui_pipei] 通知发送异常（{e}）。")
                return CustomRecognition.AnalyzeResult(box=box, detail=f"命中按钮：{why}")

            time.sleep(interval)

        logger.info("[jianhui_pipei] 本轮等待超时，未出现开始匹配按钮，继续守候。")
        return CustomRecognition.AnalyzeResult(
            box=None, detail="本轮等待超时，未出现开始匹配按钮，继续守候"
        )
