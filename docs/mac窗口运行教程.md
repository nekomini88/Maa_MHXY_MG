 # mac教程

> 1.  打开终端，解压分发的压缩包：
>     
>     **选项1：解压到系统目录（需要管理员权限）**
>     
>     ```shell
>     sudo mkdir -p /usr/local/bin/Maa_MHXY_MG
>     sudo tar -xzf <下载的Maa_MHXY_MG压缩包路径> -C /usr/local/bin/Maa_MHXY_MG
>     ```
>     
>     **选项2：解压到用户目录（推荐，无需sudo）**
>     
>     ```shell
>     mkdir -p ~/Maa_MHXY_MG
>     tar -xzf <下载的Maa_MHXY_MG压缩包路径> -C ~/Maa_MHXY_MG
>     ```
>     
> 2.  进入解压目录并运行程序：
>     
>     ```shell
>     cd /usr/local/bin/Maa_MHXY_MG
>     ./Maa_MHXY_MG
>     ```
>     
> 
> 若想使用**图形操作页面**请按第二步操作，执行 `Maa_MHXY_MG` 程序。
> 
> ⚠️Gatekeeper 安全提示处理：
> 
> 在 macOS 10.15 (Catalina) 及更高版本中，Gatekeeper 可能会阻止运行未签名的应用程序。  
> 如果遇到"无法打开，因为无法验证开发者"等错误，请选择以下任一方案:
> 
> ```shell
> # 方案1：以 Maa_MHXY_MG 为例，移除隔离属性（推荐，以实际路径为准）
> sudo xattr -rd com.apple.quarantine /usr/local/bin/Maa_MHXY_MG/Maa_MHXY_MG
> # 或用户目录版本：xattr -rd com.apple.quarantine ~/Maa_MHXY_MG/Maa_MHXY_MG
> 
> # 方案2：添加到 Gatekeeper 白名单
> sudo spctl --add /usr/local/bin/Maa_MHXY_MG/Maa_MHXY_MG
> # 或用户目录版本：spctl --add ~/Maa_MHXY_MG/Maa_MHXY_MG
> 
> # 方案3：一次性处理整个目录
> sudo xattr -rd com.apple.quarantine /usr/local/bin/Maa_MHXY_MG/*
> # 或用户目录版本：xattr -rd com.apple.quarantine ~/Maa_MHXY_MG/*
> ```