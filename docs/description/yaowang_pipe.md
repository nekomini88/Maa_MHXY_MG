# 蹲妖王（监测 + Telegram 通知）

持续监测屏幕底部聊天栏/通知栏的妖王公告。当识别到妖王刷新公告时，自动往 Telegram 推送通知，方便你及时上线剿灭。

## 前置条件

- 已在 `config/config.json` 中配置 Telegram 通知：

  ```json
  {
    "ExternalNotificationEnabled": "Telegram",
    "ExternalNotificationTelegramBotToken": "你的Bot令牌",
    "ExternalNotificationTelegramChatId": "你的chat_id"
  }
  ```

- 运行前请确保角色已登录入游戏（可以在任意主界面/挂机状态运行，纯监测不打断当前任务）。

## 运行说明

- 纯监测任务：不点击、不打断挂机/任务，只盯着底部聊天栏。
- 连续运行直到手动停止（通过任务控制台停止）。
- 识别到妖王公告 → 推送 Telegram 通知一次（含公告原文）。
- 冷却去重：默认 300 秒（可配置 `yaowang_cooldown_seconds`），同一时段不重复刷屏。

## 配置项（config/config.json）

| 配置 | 默认 | 说明 |
|---|---|---|
| `yaowang_bottom_ratio` | `0.85` | 底部聊天栏起始高度（占屏幕比例） |
| `yaowang_cooldown_seconds` | `300` | 两次通知最小间隔秒数 |
| `yaowang_expected` | 见代码 | 匹配关键词（含"妖王""妖魔冲到了"及容错变体） |

## 常见问题

- 收不到通知：检查 bot token / chat_id 是否正确，及 `ExternalNotificationEnabled` 是否为 "Telegram"。
- 想更快收到：降低 `yaowang_cooldown_seconds`。