# 擂台匹配（自动点开始匹配）

用途：打开「擂台大战」面板后，自动等待右下角「开始匹配」按钮出现并点击。

识别器 `leitai_pipei`：对按钮区 ROI 做 OCR（关键词「开始匹配」），ROI
默认按屏幕比例计算（兼容不同模拟器分辨率），最多等待 300 秒。

防误报：左下方提示文字「队伍组满5人方可开始擂台乱斗匹配」同样含
「匹配」二字——ROI 已将其隔离在外，且匹配词要求「开始匹配」全词，
不会点错。

pipeline `leitai_pipei`：命中即 Click，点击后回主界面判定；超时未出现
则循环继续等待。

可选参数（custom_recognition_param）：leitai_roi 自定义绝对像素 ROI、
leitai_max_wait 等待秒数、leitai_interval 轮询间隔、leitai_notify 命中通知。
