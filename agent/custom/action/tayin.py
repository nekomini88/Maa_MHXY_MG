"""
MAA 自定义动作：拓印自动描摹（改进版）
- 使用固定 RGB 阈值提取笔画骨架
- 基于端点遍历排序轨迹点
- 限制滑动指令条数 ≤ 10
"""

import time
import random
import numpy as np
import cv2
from skimage.morphology import skeletonize

from maa.custom_action import CustomAction
from maa.context import Context
from utils import logger
from maa.agent.agent_server import AgentServer

@AgentServer.custom_action("tayin")
class Tayin(CustomAction):
    """
    拓印自动描摹 梦幻西游拓印（固定RGB阈值，滑动次数≤10）
    """

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> CustomAction.RunResult:
        logger.info("进入tayin（改进版）")

        # 1. 截图
        image_bgr = context.tasker.controller.post_screencap().wait().get()
        if image_bgr is None:
            logger.error("截图失败")
            return CustomAction.RunResult(success=False)

        # 转换为 RGB
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

        # 2. ROI 与颜色阈值
        roi = [515, 197, 320, 320]
        lower_color = [220, 172, 132]   # R, G, B 下界
        upper_color = [233, 203, 164]   # R, G, B 上界

        # 3. 提取轨迹点（改进排序）
        turning_points = self._extract_stroke_points(image_rgb, roi, lower_color, upper_color)
        if not turning_points:
            logger.warning("未检测到有效笔画")
            return CustomAction.RunResult(success=False)
        logger.info(f"提取到 {len(turning_points)} 个轨迹点")

        # 4. 生成滑动序列
        max_swipes = 10
        swipes = self._generate_swipe_sequence(turning_points, max_swipes=max_swipes)
        if not swipes:
            logger.warning("无法生成滑动指令")
            return CustomAction.RunResult(success=False)
        logger.info(f"生成 {len(swipes)} 条滑动指令")

        # 5. 执行滑动
        for idx, (x1, y1, x2, y2) in enumerate(swipes):
            if idx > 0:
                time.sleep(random.uniform(0.1, 0.3))
            duration = random.randint(150, 250)  # 毫秒
            context.tasker.controller.post_swipe(x1, y1, x2, y2, duration=duration).wait()

        logger.info("拓印描摹完成")
        return CustomAction.RunResult(success=True)

    # ------------------------------------------------------------------
    # 以下为与本地测试一致的图像处理函数（仅做少量日志调整）
    # ------------------------------------------------------------------

    def _extract_stroke_points(self, image, roi, lower_color, upper_color):
        """
        从 ROI 中提取笔画骨架点，返回全局坐标列表（已按笔画顺序排序）
        """
        x, y, w, h = roi
        roi_img = image[y:y+h, x:x+w]

        # 阈值提取
        lower = np.array(lower_color, dtype=np.uint8)
        upper = np.array(upper_color, dtype=np.uint8)
        mask = cv2.inRange(roi_img, lower, upper)
        pixel_count = np.count_nonzero(mask)

        if pixel_count < 100:
            logger.warning(f"mask非零像素过少: {pixel_count}")
            return []

        # 闭运算填充断裂
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

        # 骨架化
        skeleton_bool = skeletonize(closed > 0)
        skeleton = (skeleton_bool * 255).astype(np.uint8)

        ys, xs = np.where(skeleton > 0)
        if len(xs) == 0:
            return []

        points_roi = np.stack([xs, ys], axis=-1)

        # 排序轨迹点（改进的端点遍历法）
        ordered = self._sort_stroke_trace(points_roi)

        # 还原全局坐标
        global_points = [(px + x, py + y) for px, py in ordered]
        return global_points

    def _sort_stroke_trace(self, points: np.ndarray):
        """
        改进的轨迹排序：将骨架拆分为独立笔画，分别对每个笔画进行端点遍历，
        然后模拟提笔跳转，确保不跨笔画乱飞。
        """
        if len(points) == 0:
            return []

        points_list = points.tolist()
        points_set = set(map(tuple, points_list))

        # 构建8邻域邻接表
        neighbors = {}
        for p in points_list:
            nbs = []
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    if dx == 0 and dy == 0:
                        continue
                    q = (p[0]+dx, p[1]+dy)
                    if q in points_set:
                        nbs.append(q)
            neighbors[tuple(p)] = nbs

        # 1. 拆分成独立的连通分量（不同笔画）
        visited_global = set()
        components = []
        for p in points_list:
            if tuple(p) in visited_global:
                continue
            comp = []
            stack = [tuple(p)]
            visited_global.add(tuple(p))
            while stack:
                node = stack.pop()
                comp.append(node)
                for nb in neighbors[node]:
                    if nb not in visited_global:
                        visited_global.add(nb)
                        stack.append(nb)
            components.append(comp)

        # 2. 对每个连通分量内部进行端点DFS遍历
        ordered_components = []
        for comp in components:
            if not comp:
                continue
            # 找该分量的端点（邻居数为1）
            endpoints = [pt for pt in comp if len(neighbors[pt]) == 1]
            start = endpoints[0] if endpoints else comp[0]

            comp_visited = set()
            ordered_comp = []
            stack = [start]
            # 迭代DFS（防止递归爆栈）
            while stack:
                node = stack.pop()
                if node in comp_visited:
                    continue
                comp_visited.add(node)
                ordered_comp.append(node)
                for nb in reversed(neighbors[node]):
                    if nb not in comp_visited:
                        stack.append(nb)
            ordered_components.append(ordered_comp)

        # 3. 模拟提笔：将各个笔画按最近距离串起来
        final_ordered = []
        while ordered_components:
            if not final_ordered:
                final_ordered.extend(ordered_components.pop(0))
                continue
            
            cur = np.array(final_ordered[-1])
            best_dist = float('inf')
            best_idx = -1
            for i, comp in enumerate(ordered_components):
                d1 = np.linalg.norm(cur - np.array(comp[0]))
                d2 = np.linalg.norm(cur - np.array(comp[-1]))
                d = min(d1, d2)
                if d < best_dist:
                    best_dist = d
                    best_idx = i

            next_comp = ordered_components.pop(best_idx)
            # 判断是从头写还是从尾写，保证笔画方向顺畅
            if np.linalg.norm(cur - np.array(next_comp[0])) <= np.linalg.norm(cur - np.array(next_comp[-1])):
                final_ordered.extend(next_comp)
            else:
                final_ordered.extend(reversed(next_comp))

        return [list(p) for p in final_ordered]

    def _generate_swipe_sequence(self, points, max_swipes=10):
        """
        从排序后的轨迹点生成滑动指令，使用 Douglas-Peucker 简化确保条数≤max_swipes
        """
        if len(points) < 2:
            return []

        pts = np.array(points, dtype=np.float32)

        if len(pts) <= max_swipes + 1:
            indices = np.linspace(0, len(pts) - 1, min(len(pts), max_swipes + 1), dtype=int)
            sampled = pts[indices].tolist()
        else:
            epsilon = 1.0
            while True:
                pts_int = pts.reshape(-1, 1, 2).astype(np.int32)
                approx = cv2.approxPolyDP(pts_int, epsilon, False)
                simplified = approx.reshape(-1, 2).tolist()
                if len(simplified) <= max_swipes + 1:
                    sampled = simplified
                    break
                epsilon *= 1.5
                if epsilon > 100:
                    indices = np.linspace(0, len(pts) - 1, max_swipes + 1, dtype=int)
                    sampled = pts[indices].tolist()
                    break

        swipes = []
        for i in range(len(sampled) - 1):
            x1, y1 = sampled[i]
            x2, y2 = sampled[i + 1]
            swipes.append((int(x1), int(y1), int(x2), int(y2)))
        return swipes




@AgentServer.custom_action("tayin_TZ")
class tayin_TZ(CustomAction):
    """
    拓印自动描摹 梦幻西游拓印（固定RGB阈值，滑动次数≤10）
    """

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> CustomAction.RunResult:
        logger.info("遇见拓印，停止任务。")

        context.tasker.post_stop().wait()

        return CustomAction.RunResult(success=True)

