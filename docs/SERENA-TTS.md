# 按需 Serena 语音

**建议开启TTS功能，使用体验更佳。**

当前消息明确要求机器人本轮用语音回复、朗读或说给自己听时，才将最终回复转成语音。例如“用语音跟我说晚安”。普通聊天、讨论 TTS 技术、否定语音、引用和转述他人要求不启用，也不会因上一轮要求语音而一直发语音。

机器人结合人设、上下文与本轮台词生成中文语气指令，交给本地 Qwen3-TTS CustomVoice 的 Serena 音色。工具过渡文字不转语音；合成失败时保留文字并说明未成功。

## 环境准备

此可选功能需要独立的本地 TTS 环境和支持 CUDA 的显卡。模型、Python 环境及 CUDA 库不随机器人安装器提供，也不会在普通聊天时自动下载。

当前已测试的环境为 Python 3.12、qwen-tts 0.1.1、PyTorch 2.11.0+cu128、Gradio 6.17.3、soundfile 0.14.0、NumPy 2.5.4。请在独立虚拟环境中安装 TTS 依赖，避免覆盖机器人使用的 NumPy。代码位于 `local_tts/serena.py`，依赖清单位于同目录 `requirements.txt`；PyTorch/CUDA 按本机显卡环境安装。

默认目录布局：

```text
D:\Qwen3-TTS\
  .venv\Scripts\python.exe
  serena.py
  models\Qwen3-TTS-12Hz-1.7B-CustomVoice\
```

复制 `local_tts/serena.py` 到上述目录。模型从 `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` 自行下载并遵守模型协议；准备好后可先运行：

```powershell
D:\Qwen3-TTS\.venv\Scripts\python.exe D:\Qwen3-TTS\serena.py --web --no-browser
```

首次运行会建立缓存、临时文件和输出目录，服务仅监听 `127.0.0.1:7865`。更换目录时，在启动 AstrBot 的环境中设置 `WHALE_TTS_ROOT` 为 TTS 所在目录，然后重启 AstrBot。

## 机器人接入

`whale_support` 已接入按需语音。服务未运行时，在确认当前消息要求语音后自动后台启动；已有服务直接复用。单卡串行合成，冷启动会等待模型加载。

使用本接入时保持 AstrBot 全局 TTS 关闭，避免两条路径重复转换；如果选择 AstrBot 其他 TTS 方案，则按对应提供商配置，避免同时使用本地 Serena 接入。

语音经 AstrBot Record 交给 NapCat，台词保留用于历史。已有实际私聊发送成功及使用者“测试通过”反馈；该结果不代表所有语气、并发或其他显卡环境都已验收。
