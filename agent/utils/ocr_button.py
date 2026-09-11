# -*- coding: utf-8 -*-
"""按钮类 OCR 的公共部件（擂台 / 竞技场 / 剑会三个「自动点开始匹配」共用）。

为什么要有这一层（v0.1.8 真机事故的根因）
------------------------------------------
1. MaaFramework 的 OCR ``expected`` 是 **boost::regex_search（子串匹配）**：
   关键词写 ``"开始匹配"`` 时，弹窗标题「请及时开始匹配」、说明行
   「点击开始匹配进入对局」、擂台左下的「队伍组满5人方可开始擂台乱斗匹配」
   都会命中（见 v5.12.2 ``Vision/OCRer.cpp`` 的 ``filter_by_required``）。
2. ``cherry_pick()`` 默认 ``order_by=Horizontal`` 取**最靠左**的命中框，
   而 ``Actuator.cpp`` 的 Click 落点又取自识别命中框（``get_target_rect``）。
   标题/说明行排在按钮左边时，Click 就点在文字上——现象正是「日志说识别
   命中，按钮却一次没被点到」。

三个同类任务都踩过这个坑，所以锚定、候选复查、截图循环集中写在这里，
避免各识别器各写一份、改一个漏一个。

用法（识别器侧）
----------------
* ``expected`` 用 ``anchored_exact("开始匹配")`` 生成锚定正则；
* 拿到识别结果后必须用 :func:`pick_button_box` 挑框，
  **不要**直接返回 ``reco.box``（那是框架的 best，可能正是标题）；
* 只有显式传了 ROI 比例时才需要先截图量尺寸，用 :func:`ratio_roi`。
"""

from __future__ import annotations

import re

# 锚定正则：只有整段文本就是「开始匹配」才算数。
EXACT_TEXT = "开始匹配"
# 这些文本也含「开始匹配」或长得很像，但都不是按钮，命中一律丢弃。
BLOCKLIST = ("请及时", "等等", "取消", "关闭", "等待")


def anchored_exact(text: str) -> str:
    """生成锚定「整段文本等于 text」的正则（交给框架的 OCR expected）。

    锚定后框架侧就不会把含关键词的标题/说明行当成命中，
    识别器拿到的候选框里自然也没有它们。
    """
    return rf"^\s*{re.escape(text)}\s*$"


ANCHORED_EXACT = anchored_exact(EXACT_TEXT)


def normalize_text(text: str) -> str:
    """去掉空白与标点，只留文字本身。

    OCR 常把按钮上的文字读成「开始匹配」「开始 匹配」「开始匹配·」这类形态，
    去掉空白/标点后再比，既不误收标题，也不漏掉按钮。
    """
    return re.sub(r"[\s\u3000\W_]+", "", text, flags=re.UNICODE)


def tasker_stopping(context) -> bool:
    """任务是否已被请求停止。

    只看 ``tasker.stopping``：``tasker.running`` 在部分版本里会被识别回调
    自身改写，用它判断会让正常轮询误以为应该收工。
    """
    return bool(getattr(getattr(context, "tasker", None), "stopping", False))


def rect_to_box(rect) -> list | None:
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


def field(cand, name, default=None):
    """候选字段读取：同时兼容 dataclass 与 raw JSON dict。"""
    if isinstance(cand, dict):
        return cand.get(name, default)
    return getattr(cand, name, default)


def candidates(reco) -> list:
    """取 OCR 候选框列表（优先过滤后的 filtered_results，其次 all_results）。

    绑定自带解析好的 dataclass；老版本没有这两个属性时退回 raw_detail JSON。
    """
    for attr in ("filtered_results", "all_results"):
        items = getattr(reco, attr, None)
        if isinstance(items, (list, tuple)):
            picked = [c for c in items if rect_to_box(field(c, "box"))]
            if picked:
                return picked

    raw = getattr(reco, "raw_detail", None)
    if isinstance(raw, dict):
        for key in ("filtered", "all"):
            items = raw.get(key)
            if isinstance(items, list):
                picked = [
                    c for c in items if isinstance(c, dict) and rect_to_box(c.get("box"))
                ]
                if picked:
                    return picked
    return []


def pick_button_box(
    reco,
    exact_text: str = EXACT_TEXT,
    blocklist=BLOCKLIST,
) -> tuple[list | None, str]:
    """从识别结果里挑出按钮框，返回 (box, 说明)。

    判据是**整段文本**（去空白标点后）等于 ``exact_text``：标题
    「请及时开始匹配」、说明行「点击开始匹配进入对局」都不满足，
    因此不会被当按钮点掉。含 ``blocklist`` 字样的文本一律排除。
    同一档内取 OCR 得分最高的，再取面积最大的。
    """
    best = None
    best_key = None
    seen_texts = []
    for cand in candidates(reco):
        text = str(field(cand, "text", "") or "").strip()
        if text:
            seen_texts.append(text)
        if not text or any(bad in text for bad in blocklist):
            continue
        if normalize_text(text) != exact_text:
            continue
        box = rect_to_box(field(cand, "box"))
        if not box:
            continue
        try:
            score = float(field(cand, "score", 0.0) or 0.0)
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
        # 有候选但一个都不是按钮（比如弹窗还在动画里）：不点，等下一轮。
        return None, "候选文本无一是按钮：" + " / ".join(seen_texts[:3])

    # 完全没有候选明细：绑定结构与预期不符时的兜底。此时框架侧已用锚定正则
    # 过滤过，box 不会落在标题上。
    box = rect_to_box(getattr(reco, "box", None))
    if box:
        return box, "兜底：框架未提供候选明细"
    return None, "无候选框"


def ratio_roi(image, ratio) -> list:
    """按比例算 ROI（显式要求时才用，默认全屏免换算）。"""
    h, w = image.shape[:2]
    rx, ry, rw, rh = ratio
    return [int(w * rx), int(h * ry), int(w * rw), int(h * rh)]
