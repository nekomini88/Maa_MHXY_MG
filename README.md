<!-- markdownlint-disable MD033 MD041 -->
<p align="center">
  <img alt="LOGO" src="./docs/img/logo.png" width="220" />
</p>

<div align="center">

# MAA_MHXY_MG

![GitHub stars](https://img.shields.io/github/stars/gitlihang/Maa_MHXY_MG?style=flat-square)
![GitHub release](https://img.shields.io/github/v/release/gitlihang/Maa_MHXY_MG?style=flat-square)
![License](https://img.shields.io/github/license/gitlihang/Maa_MHXY_MG?style=flat-square)

基于 MaaFramework 的“梦手”自动化助手，结合图像识别与模拟控制，帮助完成高频日常与部分周常任务。

</div>

## 目录
- [项目简介](#项目简介)
- [功能列表](#功能列表)
- [教程文档](#教程文档) 
- [鸣谢](#鸣谢)
- [免责声明](#免责声明)
- [沟通交流](#沟通交流)
- [赞助](#赞助)

## 项目简介

`MAA_MHXY_MG` 是一个面向“梦手”的项目，依托 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 实现。



> <b>重点说明：由于 69 级以下账号操作逻辑不同，部分功能对低等级小号的适配仍存在兼容性不足。</b>


## 功能列表

已完成的代表性功能包括：
- 签到、师门、秘境等日常任务
- 周长任务中的多项内容

完整功能明细请查看：[功能列表](./docs/功能列表.md)

## 教程文档

- [窗口运行教程](./docs/窗口运行教程.md)
- [CMD 运行教程](./docs/CMD运行教程.md)
- [功能列表](./docs/功能列表.md)

## 配置文件

仓库里只放**模板** `config/config.example.json`；运行时用的配置不入库（会填真实 token，也免得升级时被覆盖）。首次使用先复制一份：

```bash
cp config/config.example.json config/config.json
```

- `config/config.json`：运行期配置。外部通知要填 `ExternalNotificationTelegramBotToken` / `ExternalNotificationTelegramChatId`（`ExternalNotificationEnabled` 设成 `Telegram`）。
- `config/notify.json`：可选的**明文**通知覆盖，MFA 不管理它，非空值优先于 `config.json`（MFA 会把界面里填的 token 加密写回 `config.json`，所以想长期生效建议写在这里）。
- 打包/安装脚本 `tools/install.py` 会把 `config.example.json` 自动复制进安装包，用户只需改动包内的 `config/config.json`。

## 鸣谢

本项目由 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 提供强力支持，感谢所有参与和支持项目开发的贡献者！

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework)
- [MFAAvalonia](https://github.com/MaaXYZ/MFAAvalonia)
- [MXU](https://github.com/MistEO/MXU)


## 免责声明

本项目仅供学习与交流参考。

## 沟通交流

- QQ 群：`953819042`

## 赞助

<a href="https://afdian.com/a/gitlihang">
  <img width="200" src="https://pic1.afdiancdn.com/static/img/welcome/button-sponsorme.png">
</a>
