# 蹲妖王（监测 + Telegram 通知）

持续监测屏幕左侧[聊天栏]区域的妖王公告。当识别到妖王刷新公告时，自动往 Telegram 推送通知，方便你及时上线剿灭。

> 妖王公告形态：系统频道会连续滚动三条消息「xxx在挖宝时放出了远古妖王」「妖魔冲到了xx城（x级可挑战），大家快去击败妖王啊」。它们出现在挂机主界面**左侧聊天栏**的中上部区域，持续停留较久。

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

- 纯监测任务：不点击、不打断挂机/任务，只盯着左侧聊天栏。
- 连续运行直到手动停止（通过任务控制台停止）。
- 识别到妖王公告 → 推送 Telegram 通知一次（含公告原文）。
- 冷却去重：默认 60 秒（可配置 `yaowang_cooldown_seconds`），避免三条连发刷屏。

## 配置项（config/config.json）

| 配置 | 默认 | 说明 |
|---|---|---|
| `yaowang_chat_ratio_x` | `0.45` | 聊天栏宽度占全图宽比例 |
| `yaowang_chat_ratio_y` | `0.18` | 聊天栏顶部占全图高比例 |
| `yaowang_cooldown_seconds` | `60` | 两次通知最小间隔秒数（避免三条连发刷屏） |
| `yaowang_expected` | 见代码 | 匹配关键词（含"妖王""妖魔冲到了""远古妖王""击败妖王"及容错变体） |

## 常见问题

- 收不到通知：检查 bot token / chat_id 是否正确，及 `ExternalNotificationEnabled` 是否为 "Telegram"。
- 想更快收到：降低 `yaowang_cooldown_seconds`。