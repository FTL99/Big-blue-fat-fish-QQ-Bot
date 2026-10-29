# B站视频资料读取

支持群聊中的 BV/av、b23 短链和 QQ 分享卡，取得可访问的视频详情、指定分P字幕、抽样画面，以及可取得的评论、弹幕。资料供理解分享意图和接话使用，分享视频不自动等于要求摘要或心理分析。

本轮链接优先，按 BVID、CID、分P绑定证据；没有唯一目标时澄清，不把旧视频内容混入新视频。需要先接话时，由当前人格和意图决定回应内容，随后后台读取，不固定使用同一句回执。

字幕、少量故事板帧与评论各有来源边界。没有字幕或图片失败时明确缺失；本版未集成本地视频 ASR，不声称看完整段视频。受限、下架或需要额外权限的视频可能不可读。

## 可选扫码登录

字幕可能需要登录。安装源码依赖后执行（将安装目录换成自己的路径）：

```powershell
python scripts/bilibili_login.py generate --install-root D:\qqaibot
python scripts/bilibili_login.py wait --install-root D:\qqaibot
```

第一步打印本地二维码图片位置，手机确认后第二步保存必要登录字段。不会读取浏览器 Cookie，也不会打印登录凭据。

凭据位于 `<安装目录>/astrbot/data/plugin_data/astrbot_plugin_group_chat_plus/bilibili/credential.json`。扫码临时文件位于源码目录的 `tests/private/`，二维码位于 `tests/results/`；这些目录均不提交。不要上传运行目录、Cookie 或二维码。

上游来源和 MIT 署名见插件内 `THIRD_PARTY_BILIBILI_NOTICE.md`。API 可用性随站点变化，取得资料不代表已验证每一帧或每句台词。
