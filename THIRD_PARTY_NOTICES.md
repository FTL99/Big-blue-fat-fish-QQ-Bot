# 第三方来源与本地修改

本项目是多个独立组件的整合；各组件许可证保持有效，根目录 MIT 仅适用于有权授权的独立新增内容。

| 路径/依赖 | 上游 | 版本 | 许可 |
|---|---|---|---|
| scripts/bot_manager.py、persona/upstream-default.md | [mtshang/make-your-qq-ai-group-member](https://github.com/mtshang/make-your-qq-ai-group-member) | 2026-10 本地部署来源 | MIT，原版权 mtshang；全文见 licenses/deployment-MIT.txt |
| AstrBot 安装依赖 | [AstrBotDevs/AstrBot](https://github.com/AstrBotDevs/AstrBot) | 4.29.1 | AGPL-3.0；licenses/AstrBot-AGPL-3.0.txt；此次核对 wheel RECORD 未发现本机框架 Python 源码改动 |
| plugins/astrbot_plugin_group_chat_plus | [Him666233](https://github.com/Him666233/astrbot_plugin_group_chat_plus) | V1.2.3.hotfix.2，本地修改版 | AGPL-3.0，保留目录内 LICENSE 与原作者声明 |
| plugins/astrbot_plugin_livingmemory | [lxfight](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory) | 2.6.1 | AGPL-3.0，保留目录内 LICENSE |
| plugins/astrbot_plugin_meme_manager | [anka](https://github.com/anka-afk/astrbot_plugin_meme_manager) | 5.1.0，来源 commit 42104fed0ee2644e6a7e246b48892b8dc7ee361b，本地修改版 | MIT，保留目录内 LICENSE |
| plugins/astrbot_plugin_whale_stickers、plugins/astrbot_plugin_whale_support | 本项目新增 | 2026-10-21 整理版 | 独立代码及重新撰写的图像描述 MIT；图片另见 ASSET_LICENSES.md |
| 模型/音频运行依赖 | BAAI/Xenova、FunASR/SenseVoice、FFmpeg 等 | provenance 记录及 requirements.txt | 各自独立许可；不随源码包提供权重或二进制 |

FTL99 在 2026-10-20 至 2026-10-21 的主要修改：group_chat_plus 上下文去重/排序与追问规则、延迟图片描述及参考匹配、私聊原生流程兼容、斗图路由；meme_manager 关闭竞争发送路径、收藏形象筛选与私聊隔离；bot_manager 后台生命周期与启动诊断。2026-10-21 发布整理仅将部署专用账号改为本地配置，未借此把上游代码重新标为 MIT。

每个插件目录提供可编辑源码，包括其后端和前端源文件；源文件及生产哈希记录在 provenance/code-origin.json。测试时使用官方 pip AstrBot 4.29.1。原插件图标和未单独核查的字体文件未纳入本次整理；WebUI 个别装饰资源可能回退，功能以验收说明为准。

AGPL 组件及其修改继续适用原协议的源码提供、许可和修改声明要求。发布或对外运行修改版时，应把实际对应版本的仓库/归档地址提供给用户。AGPL 不禁止商业使用，不在它的代码上另加非商业限制；美术 NC 条款单独适用。

运行依赖入口：[NapCat v4.18.38 LICENSE](https://github.com/NapNeko/NapCatQQ/blob/v4.18.38/LICENSE)、[BGE 原模型](https://huggingface.co/BAAI/bge-small-zh-v1.5)、[Xenova ONNX 模型](https://huggingface.co/Xenova/bge-small-zh-v1.5)、[SenseVoice 模型卡](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)、[FunASR MODEL_LICENSE](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)、[FFmpeg 许可](https://ffmpeg.org/legal.html)。


## 2026-10 后续修改

会话理解及按需检索、B站资料绑定与隔离属于 group_chat_plus / LivingMemory 的修改，继续遵守各自 AGPL。B站字幕与故事板流程参考 c1overzz/dsh-bili-scroll 的固定提交 4af98392ee078e02f50a7fd0bf0491033f4675ed；完整 MIT 署名与许可见 `plugins/astrbot_plugin_group_chat_plus/THIRD_PARTY_BILIBILI_NOTICE.md`。

本项目新增的 `local_tts/serena.py` 与 `whale_support/voice.py` 为本地语音接入代码，按根目录 MIT 发布。运行时另需 Qwen3-TTS、PyTorch、Gradio 等依赖及模型权重，各自协议独立；本仓库不重分发模型、CUDA 二进制、参考声音或生成音频。模型来源：Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice。
