# 竞技场挂机（自动点开始匹配）

用途：打开「竞技场」面板后，自动等待右下角「开始匹配」按钮出现并点击，
打完一场回来继续蹲，循环挂机。

识别器 `jingjichang`：对右下按钮区 ROI 做 OCR（关键词「开始匹配」），ROI
默认按屏幕比例计算（兼容不同模拟器分辨率），单轮最多等待 600 秒
（一场战斗的时间），轮询间隔 2 秒。

pipeline `jingjichang`：命中即 Click，点完跳回继续等下一场；超时未出现
也跳回继续蹲，直到手动停止任务。

可选参数（custom_recognition_param）：jjc_roi 自定义绝对像素 ROI、
jjc_max_wait 等待秒数、jjc_interval 轮询间隔、jjc_notify 命中通知。
