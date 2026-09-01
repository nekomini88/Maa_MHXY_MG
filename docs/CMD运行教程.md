## 下载打包的程序

1.下载对应系统（Android、win、macos）版本的X86，arm\
2.解压后运行 `MaaPiCli.exe` 即可\
3.MUMU模拟器12，1280*720(其他16：9分辨率也可) 240dpi
## 建议使用窗口教程

[窗口运行教程](./docs/窗口运行教程.md)

## 使用方法
### ①
启动后会出现:
```
Welcome to use Maa Project Interface CLI!

Version: v0.0.1

### Select ADB ###

        1. Auto detect
        2. Manual input

Please input [1-2]:
```
分别代表：
1. 自动检测，如果在同一台机器上开启着模拟器，可以尝试选择这个。

```
### Select ADB ###

        1. Auto detect
        2. Manual input

Please input [1-2]: 1

Finding device...

## Select Device ##

        1. MuMuPlayer12
                H:/Program Files/Netease/MuMuPlayer-12.0/shell/adb.exe
                127.0.0.1:16672

Please input [1-1]: 1
```
选择 1 后会像上面这样，列出若干个模拟器实例，之后选择你需要进行操控的即可。

2. 手动输入，目前手动输入对于"含空格的路径"有问题，请参考下面的说明手动修改 `config/maa_pi_config.json`，如果没有则新建，修改完成后重启 `MaaPiCli`。

```
{
    "adb": {
        "adb_path": "C:/Program Files/Netease/MuMuPlayer-12.0/shell/adb.exe",
        "address": "127.0.0.1:16416",
        "config": {
            "extras": {
                "mumu": {
                    "enable": true,
                    "index": 1,
                    "path": "C:/Program Files/Netease/MuMuPlayer-12.0"
                }
            }
        }
    },
    "controller": {
        "name": "安卓端"
    },
    "resource": "官服",
    "task": [
        {
            "name": "福利签到(已完成)",
            "option": [
            ]
        }
    ],
    "win32": {
        "_placeholder": 0
    }
}
```
其中 `"adb_path"` 部分是你的adb的路径，一般你可以在你的MuMu等模拟器的安装路径下，找到 `adb.exe`。注意如果路径里面有`\`，需要替换成 `\\'` ，就像上面那样。

`address` 是模拟器实例的 ADB 端口信息，你可以从你的模拟器上获取，获取方式相关可以参考：[MAA的文档](https://maa.plus/docs/%E7%94%A8%E6%88%B7%E6%89%8B%E5%86%8C/%E5%B8%B8%E8%A7%81%E9%97%AE%E9%A2%98.html#%E7%A1%AE%E8%AE%A4-adb-%E5%8F%8A%E8%BF%9E%E6%8E%A5%E5%9C%B0%E5%9D%80%E6%AD%A3%E7%A1%AE)。

### ②
在初次启动后，会让你输入启动的任务：
```
### Add task ###

        1. 普通任务
        2. 启动游戏
        3. 帮派签到(已完成)
        4. 整理背包(已完成)
        5. 福利签到(已完成)
        6. 师门任务
        7. 师门任务2(已完成)
        8. 秘境降妖（已完成）
        9. 宝图任务(已完成)
        10.挖宝图(已完成)
        11.运镖普通(已完成)

Please input multiple [1-9]:
```
选择你要执行的任务即可。

### ③

之后会反复出现：
```
Tasks:

<这里会列出你已经增加，等待执行的任务>

### Select action ###

        1. Switch controller
        2. Switch resource
        3. Add task
        4. Move task
        5. Delete task
        6. Run tasks
        7. Exit

Please input [1-7]: 
```
    其中分别代表：
    1. 调整控制器（也就是adb地址等）
    2. 调整资源（不用管，目前只有一个）
    3. 新增任务，像②中那样
    4. 移动任务
    5. 删除任务
    6. 开始执行任务，在这之后就会自动开始操控。
    7. 退出

# 演示示例
![alt text](img/image-2.png)