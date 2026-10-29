# Windows 安装、启动与回退

## 范围

适用 Windows、Python 3.12、AstrBot 4.29.1。增强功能默认服务一个群和管理员私聊。安装到全新空目录，源代码目录与运行目录分开；已有机器人不迁移、不停机、不覆盖。不要直接指定一个已在运行的安装目录。

准备 QQ 小号、可用的大模型 API Key、群号、管理员 QQ，以及一个独立的 NapCat Shell 安装。QQ/NapCat 不包含在本仓库；从 [NapCat 官方发布页](https://github.com/NapNeko/NapCatQQ/releases)获取，遵守它的独立许可。当前管理器适配提供 napcat.bat、napcat.quick.bat 的 Shell 目录，首次登录和日常启动分别使用这两个入口；不同发行包没有这些入口时，先按该版本官方文档完成安装适配，不要将主号 QQ 目录冒充机器人目录。

仓库默认模型配置来自实际部署：文本 deepseek/deepseek-flash、视觉 deepseek/deepseek-vision，模型请求名称为 deepseek-flash。不同账号/供应商的实际可用模型、图片权限和搜索权限可能不同，安装后在 AstrBot 面板确认或替换；配置 ID 不是模型能力保证。

## 新安装

在本仓库目录打开 PowerShell，以下编号都是示例，必须替换为你自己的信息：

```powershell
python scripts/install.py --install-root D:\whale-bot --group-id 20001 --admin-qq 20002 --bot-qq 20003 --napcat-dir D:\NapCat.Shell
```

API Key 将以隐藏输入询问；自动化也可通过 WHALE_API_KEY 环境变量传入，切勿把真实 Key 写进命令、脚本或 Git 文件。不要使用真实 Key 录制终端示例。

安装器会建立相邻的 D:\whale-bot-python 虚拟环境，安装 requirements.txt 依赖，下载并校验本地 BGE/SenseVoice 模型，复制本版插件与脱敏配置，建立全新数据库并写入 V2 人格卡，生成后台管理入口。下载失败或哈希不符会报错，不把未校验文件当成可用模型。失败时目录可能保留用于排查；它不会自动删除旧数据或自动启动。

若已有独立配置好的测试 Python，可用 --prepare-only 跳过依赖和模型下载；此模式仍生成数据配置并通过框架 API 建库，需要当前 Python 已装 AstrBot。它不是一台新电脑的完整安装。离线准备后，补齐 provenance 清单中模型文件，再启动。

## 标准形象参考

两张网络参考原图不随仓库发布。若你有已获许可的图片，在安装命令末尾重复指定：

```powershell
python scripts/install.py --install-root D:\whale-bot --group-id 20001 --admin-qq 20002 --bot-qq 20003 --napcat-dir D:\NapCat.Shell --reference D:\pictures\whale-chibi.png --reference D:\pictures\whale-maid.png
```

安装时须同时提供两张 PNG：先 Q 版、再常规比例。图片只复制到本地运行目录，分别命名为 whale-chibi.png、whale-maid.png。缺少参考图时，普通图像描述仍可使用视觉模型，但不能声称已完成参考图对照识别，自动收图默认关闭。安装后也可在 astrbot/data/plugin_data/astrbot_plugin_group_chat_plus/identity 放入这两个 PNG 文件，维护 references.json 的 images 列表，并在 meme_manager 配置中启用 auto_collect；随后重启。只放在参考目录根下，不接受越级路径。

## 扫码与日常运行

先启动 AstrBot，再按 NapCat 官方教程登录，配置 OneBot 11 反向 WebSocket：

```powershell
& 'D:\whale-bot-python\Scripts\python.exe' 'D:\whale-bot\.bot_runtime\bot_manager.py' start
```

目标地址为 ws://127.0.0.1:6199/ws。连接令牌取本地 astrbot/data/cmd_config.json 中 platform[0].ws_reverse_token，填入 NapCat 的对应配置；不要将整个文件截图公开。QQ 第一次登录需本人扫码。需要重新显示扫码入口时运行管理器 scan；日常 start 使用 quick 入口，是否免扫码取决于 QQ 登录态。

面板地址为 http://127.0.0.1:6185。本机随机生成的登录信息位于安装目录 dashboard-login.local.json；此文件包含凭据，不要上传。默认只监听本机，不直接暴露公网。

双击运行目录“后台启动.vbs”可隐藏启动，桌面快捷方式也可指向它。“查看状态.vbs”和“停止机器人.vbs”提供对应操作。后台管理指 AstrBot/NapCat 启动命令窗口隐藏；扫码、QQ 客户端自身窗口行为受协议端影响，不保证整个 QQ 客户端始终无界面。

源码下载后不会自行开机启动，也不会自动给群发测试消息。首次启动若依赖、模型或 QQ 连接失败，先看 .bot_runtime 下日志，分享前删除密钥、令牌、聊天正文和个人信息。

## 验收

1. 面板确认默认人格为 whale-v2，正文对应 persona/whale-v2.md；读空气配置的近期窗口为 50、初始参与概率为 0.3。
2. 插件列表确认读空气、LivingMemory、meme_manager、whale_stickers、whale_support 初始化成功；向量模型与语音模型无缺失提示。
3. 状态确认 WebUI、反向 WebSocket、QQ 连接分别就绪。只有端口打开不能证明 QQ 已登录或消息已送达。
4. 由使用者在自己的测试会话验证：追问、称呼更正、语音、图片描述、表情与连续斗图；观察发言对象、重复发送、收尾与认真话题退出。公开发布的离线测试不代替这一步。
5. 断开网络/重启后确认错误能排查且数据仍保留，不把模型失败误认为人设拒答。

## 更新与回退

本版只提供新目录安装，不自动合并旧库或覆盖旧配置。已有部署保留，另建新安装验证后再由本人决定切换，避免同一个 QQ 同时登录两个协议进程。未经检查不要在面板自动升级修改过的 group_chat_plus/meme_manager，否则可能覆盖本版修复。

更新前停服，备份插件源码、运行配置、data_v4.db 与 plugin_data；备份包含隐私和凭据，只在本机保存。回退时停掉新版本，恢复同一时间点的源码/配置/数据后再启动。不要把运行目录的数据库与本仓库源码混为一谈。

## 公开发布边界

只提交源码仓库中的文件。安装目录、相邻 Python 环境、模型、QQ/NapCat、面板凭据、聊天记录和本地收藏都不进入 Git。源码的 .gitignore 是辅助保护，不能代替提交前检查；不要使用 git add D:\whale-bot 一类把运行目录整体纳入版本控制的做法。


## 可选语音与视频读取

建议开启TTS功能，使用体验更佳。按 [TTS 说明](SERENA-TTS.md)配置本地 Serena，或使用 AstrBot 支持的其他 TTS；避免同时启用两条发送路径。TTS 模型和 CUDA 环境需另外准备，安装器不会自动下载。B站分享卡、字幕及本地扫码登录方式见 [视频读取](BILIBILI-READ.md)。
