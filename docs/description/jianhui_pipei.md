# 剑会匹配（自动点开始匹配 · 常驻不结束）

用途：剑会组队房间内，所有人准备后弹出「请及时开始匹配」确认框时，
自动点击右侧「开始匹配」按钮（左侧「我再等等」不点）。

识别器 `jianhui_pipei`：对**全屏**做 OCR，只认整段文本就是「开始匹配」的框，
单轮最多守候 300 秒，轮询间隔 1 秒。命中后由 pipeline 节点 Click 点击。

## 为什么不是「关键词包含」+ 固定 ROI（v0.1.8 的真机问题）

v0.1.8 及之前用 `expected: ["开始匹配","开始匹","始匹配"]` + 固定比例 ROI，
真机上出现「识别日志命中、按钮却一次没点到」。根因在 MaaFramework 的 OCR：

- `expected` 走的是 **boost::regex_search 子串匹配**，`"开始匹配"` 会同时命中
  弹窗标题「请及时开始**匹配**」和说明行「点击**开始匹配**进入对局」；
- `cherry_pick()` 默认 `order_by=Horizontal`，取**最靠左**的结果 —— 标题在
  按钮左边，于是框架选中的命中框是标题文字，Click 点在了标题上。

现在的做法：`expected` 用锚定正则 `^\s*开始匹配\s*$`，再对 OCR 候选框逐个复查
（整段文本去空白/标点后必须等于「开始匹配」，含「请及时/等等/取消/关闭/等待」
的一律丢弃），ROI 默认全屏、与分辨率和横竖屏无关。

## 任务不会自己结束

点完「开始匹配」后任务**继续常驻**，不收敛、不报「任务已全部完成」：
pipeline 里 `剑会-保持匹配` 节点的 `next` 与 `on_error` 都自跳回本节点
（`[JumpBack]剑会-保持匹配`），节点 `timeout` 为 `-1`。因此

- 弹窗再次出现（队友取消、匹配失败重试、打完一局回到房间）→ 再点一次；
- 单轮 300 秒没等到按钮 → 不算失败，下一轮接着守。

要结束任务，在 MFAAvalonia 里点「停止任务」即可：识别器每个轮询点都会
检查停止标志，一两个轮询点内就退出（不会卡满 300 秒守候）。

## 使用前提

请先在剑会房间准备好、弹出过确认框的界面上启动本任务。任务启动后如果
按钮一直没出现会一直等待——这是设计如此，不是卡死。

## 参数

pipeline 节点 `custom_recognition_param` 可覆盖：

- `jianhui_enabled`：是否启用（默认 `true`）
- `jianhui_expected`：OCR 关键词（默认 `["^\s*开始匹配\s*$"]`，**必须锚定**）
- `jianhui_roi`：自定义 ROI `[x,y,w,h]` 绝对像素（默认全屏 `[0,0,0,0]`）
- `jianhui_roi_ratio`：按屏幕比例算 ROI `[x,y,w,h]`，显式给出才生效
- `jianhui_threshold`：OCR 置信度阈值（默认 `0.6`）
- `jianhui_max_wait`：单轮最长守候秒数（默认 `300`）
- `jianhui_interval`：轮询截图间隔秒（默认 `1.0`）
- `jianhui_click_cooldown`：两次命中之间的最小间隔秒（默认 `2.0`，防连点）
- `jianhui_notify`：命中是否发通知（默认 `false`）

## 本地验证（不需要真机）

`tools/dev/button_e2e_check.py` 用合成截图 + MaaFw 的 `CustomController`
冒充设备，把仓库真实的 pipeline 与识别器跑一遍，检查 Click 是否落在按钮矩形内
（擂台 / 竞技场 / 剑会三个任务共用这个脚本，`--task` 选任务）：

```bash
/root/.venv-maacheck/bin/python tools/dev/button_e2e_check.py --task jianhui --wait 12
```

三个场景：横屏 1280x720 含按钮、竖屏 720x1280 含按钮、只有诱饵标题（不该点）。
`--recognition /path/to/old.py` 可把识别器换成旧实现，复现「点在文字上」。
`tools/dev/jianhui_probe.py` 是排查用探针，打印框架 OCR 的候选框与最终选中框。
