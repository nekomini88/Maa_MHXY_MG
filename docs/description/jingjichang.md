# 竞技场挂机（自动点开始匹配，打满10次结束）

用途：打开「竞技场」面板后，自动等待右下角「开始匹配」按钮出现并点击，
打完一场回来继续蹲，打满 10 次任务完成。

识别器 `jingjichang`：对右下按钮区 ROI 做 OCR（关键词「开始匹配」），ROI
默认按屏幕比例计算（兼容不同模拟器分辨率），单次按钮等待最多 600 秒
（一场战斗的时间），轮询间隔 2 秒。点击在识别器内直接执行（点按钮中心），
计数满 `jjc_max_clicks`（默认 10）即返回成功；中途超时返回失败由 pipeline
重试，计数保留不丢。

pipeline `jingjichang`：成功（打满）回主界面判定；失败跳回继续蹲。

可选参数（custom_recognition_param）：jjc_max_clicks 打满次数、
jjc_roi 自定义绝对像素 ROI、jjc_max_wait 单次等待秒数、
jjc_interval 轮询间隔、jjc_click_delay 点击后等待、jjc_notify 每次点击通知。
