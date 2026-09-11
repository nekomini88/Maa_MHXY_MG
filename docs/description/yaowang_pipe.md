# 蹲妖王（监测 + Telegram 通知）

持续监测屏幕左侧[聊天栏]区域的妖王公告。当识别到妖王刷新公告时，自动往 Telegram 推送通知，方便你及时上线剿灭。

> 妖王公告形态：系统频道会连续滚动三条消息「xxx在挖宝时放出了远古妖王」「妖魔冲到了xx城（x级可挑战），大家快去击败妖王啊」。它们出现在挂机主界面**左侧聊天栏**的中上部区域，持续停留较久。

## 前置条件

通知的 token / chat_id 有两个来源，**任填其一**（都不填则只监测不推送）：

1. **MFA 界面里填过就够了（推荐）**：在 MFAAvalonia「设置 → 外部通知 → Telegram」里填 Bot Token 与 Chat Id，
   点「测试」能收到消息即可。agent 会直接读 MFA 写下的配置并自动解密。

   > 原理：`config/config.json` 就是 MFA 的 "Default" 配置本体（MFA 内部 Default → 文件名 `config`），
   > 用户在 MFA 界面填的 token / chat_id 会被 MFA 用 Windows DPAPI 加密后写回该文件
   > （`AQAAANCMnd8BFdERjHoAwE/Cl+sB...` 这种 300+ 字符的 base64）。agent 会按
   > DPAPI → AES 设备密钥的顺序解回明文再发送（与 MaaGumballs 不思议迷宫小助手的实现同一套解密链）。

2. **明文写进 `config/notify.json`**（MFA 不管理这个文件，永不被加密/覆盖）：

   ```json
   {
     "ExternalNotificationTelegramBotToken": "123456789:AAH...",
     "ExternalNotificationTelegramChatId": "7200170648"
   }
   ```

   非空值优先于 `config/config.json`。只有自动解密失败时才需要它，例如把配置从别的电脑拷过来
   （DPAPI 密文只能由写入它的那台机器 + 那个 Windows 用户解开）。

另外确认 `config/config.json` 里 `ExternalNotificationEnabled` 是 `"Telegram"`（这一项是明文，不受加密影响）。

- 想验证配置：双击包内 **`检查通知配置.bat`**（或 `python\python.exe tools\check_notify.py`），
  它会打印实际生效的值（打码）、说明来源是明文还是 MFA 密文，再调 `getMe` 验证 token 并真发一条测试消息。
- 运行前请确保角色已登录入游戏（可以在任意主界面/挂机状态运行，纯监测不打断当前任务）。

## 运行说明

- 纯监测任务：不点击、不打断挂机/任务，只盯着左侧聊天栏。
- 连续运行直到手动停止（通过任务控制台停止）。
- 识别到妖王公告 → 推送 Telegram 通知一次（含公告原文）。
- 冷却去重：默认 60 秒（可配置 `yaowang_cooldown_seconds`），避免三条连发刷屏。
- **掉线自愈**：控制器报告断连、或画面连续静止 180 秒、或截图连续失败 30 次 → 判定截图链路失效 →
  发一条 Telegram 告警并结束当轮，下一轮立刻重新取帧；画面恢复后再补一条「已恢复」。
  链路失效期间不会空跑到扫描上限（旧版实测盲跑过 49 分钟）。
- **监控不会因掉线而失败退出**：管线里的 `yaowang_pipe` / `蹲妖王-监测循环` 都是
  `timeout: -1`（无限等待），且监测任务末尾不再挂「判断主界面」这类必须新截图的收尾节点，
  所以模拟器掉线不会把整条任务拖成 `Tasker.Task.Failed` —— 那是旧版「跑一段时间就失败」的根因。
- 任务停止（MFA 点停止/切任务）时当前这一轮会在几百毫秒内退出，不会等满扫描上限。

## 配置项（config/config.json）

| 配置 | 默认 | 说明 |
|---|---|---|
| `yaowang_chat_ratio_x` | `0.45` | 系统公告栏宽度占全图宽比例 |
| `yaowang_chat_ratio_y` | `0.18` | 系统公告栏顶部占全图高比例 |
| `yaowang_frame_interval` | `0.25` | 帧差扫描间隔秒（检测聊天栏是否有新消息） |
| `yaowang_frame_diff` | `3000` | 判定"有变化"的像素差阈值 |
| `yaowang_cooldown_seconds` | `60` | 两次通知最小间隔秒数 |
| `yaowang_expected` | 见代码 | 出现类关键词（"妖王""远古妖王""妖魔冲到了"） |
| `yaowang_exclude` | 见代码 | 结束类排除词（"战胜""已击败"等，见备注） |
| `yaowang_max_scan` | `200` | 单轮最大扫描次数；到点干净收尾，管线立刻续下一轮 |
| `yaowang_freeze_seconds` | `180` | 画面连续静止多少秒判定截图链路失效 |
| `yaowang_screencap_fail_limit` | `30` | 截图连续失败多少次判定链路失效 |
| `yaowang_link_alert_cooldown` | `1800` | 同类链路故障的最小告警间隔秒（防刷屏） |

> 排除说明：妖王**出现**（"放出了远古妖王""妖魔冲到了xx城可挑战"）才通知；妖王**被战胜**（"xxx战胜了妖王后被击败"）不通知。

## 常见问题

- 收不到通知：先双击包内「检查通知配置.bat」，它会直接告诉你卡在哪一步（日志里 `404` → token 无效；`400` → chat_id 不对或还没私聊过该 bot）。
- 日志提示「读到的是 MFAAvalonia 加密的密文，本机 DPAPI 解不开」：说明配置是别的电脑写的，把明文填进 `config/notify.json` 即可。
- 排查用日志在 `debug/custom/`：妖王日志 `yaowang.log` 单文件超过 20 MB 自动轮转、轮转后压成 zip、只保留 7 天；日期日志保留 2 周。整套目录不会无限增长。
- **收到「妖王监控异常」告警**：说明画面卡住不动或截图一直失败，通常是模拟器卡死 / ADB 掉线 / MFA 正在重置控制器。监测本身没退出，画面恢复后会自动补发「妖王监控已恢复」；若长时间不恢复，重启模拟器即可。日志里对应 `画面已连续静止 Ns ... 判定截图链路失效` 或 `截图连续失败 N 次 ...`。
- 想更快收到：降低 `yaowang_cooldown_seconds`。