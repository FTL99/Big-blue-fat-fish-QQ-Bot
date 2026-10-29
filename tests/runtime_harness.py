"""Use candidate functions and request hooks without QQ or live DB writes."""
import importlib
import os
from pathlib import Path
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
os.environ["ASTRBOT_ROOT"] = str(ROOT / "tests/results/isolated-runtime")
os.environ["PYTHONIOENCODING"] = "utf-8"

def package(name, path):
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module

fixture_data = Path(os.environ["ASTRBOT_ROOT"]) / "data"
fixture_data.mkdir(parents=True, exist_ok=True)
(fixture_data / "whale-settings.json").write_text('{"group_id":"10001","admin_qq":"10002"}', encoding="utf-8")

package("candidate_gcp", ROOT / "plugins/astrbot_plugin_group_chat_plus")
sys.modules["astrbot_plugin_group_chat_plus"] = sys.modules["candidate_gcp"]
main = importlib.import_module("candidate_gcp.main")
cm_module = importlib.import_module("candidate_gcp.utils.context_manager")
sem = importlib.import_module("candidate_gcp.utils.semantic_understanding")
media = importlib.import_module("candidate_gcp.utils.history_media")
deferred = importlib.import_module("candidate_gcp.utils.deferred_images")
decision = importlib.import_module("candidate_gcp.utils.decision_ai")
reply = importlib.import_module("candidate_gcp.utils.reply_handler")
for name, path in [
    ("candidate_lmem", "plugins/astrbot_plugin_livingmemory"),
    ("candidate_lmem.core", "plugins/astrbot_plugin_livingmemory/core"),
    ("candidate_lmem.core.event_handler_modules", "plugins/astrbot_plugin_livingmemory/core/event_handler_modules"),
]:
    package(name, ROOT / path)
recall_module = importlib.import_module("candidate_lmem.core.event_handler_modules.memory_recall")
adapter_module = importlib.import_module("candidate_lmem.core.utils.injection_adapter")
package("candidate_support", ROOT / "plugins/astrbot_plugin_whale_support")
support_module = importlib.import_module("candidate_support.main")
package("candidate_stickers", ROOT / "plugins/astrbot_plugin_whale_stickers")
stickers_module = importlib.import_module("candidate_stickers.main")

from astrbot.api.platform import MessageType
from astrbot.api.provider import ProviderRequest
from astrbot.api.event import MessageEventResult
from astrbot.core.agent.message import TextPart


class Event:
    unified_msg_origin = "offline:GroupMessage:test-group"
    session_id = unified_msg_origin
    def __init__(self, text): self.text, self.extra = text, {}
    def get_sender_id(self): return "alice"
    def get_sender_name(self): return "用户甲"
    def get_self_id(self): return "bot-id"
    def get_group_id(self): return "test-group"
    def get_platform_id(self): return "offline"
    def get_platform_name(self): return "aiocqhttp"
    def is_private_chat(self): return False
    def get_message_type(self): return MessageType.GROUP_MESSAGE
    def get_message_str(self): return self.text
    def get_extra(self, key, default=None): return self.extra.get(key, default)
    def set_extra(self, key, value): self.extra[key] = value
    def request_llm(self, **kwargs): return kwargs
    def plain_result(self, text):
        if text.startswith("生成回复时发生错误"):
            raise AssertionError("Unexpected error result: " + text)
        return MessageEventResult().message(text)
    async def send(self, *args, **kwargs): raise AssertionError("QQ sends are forbidden in replay")


class Config:
    filtering_settings = {"use_persona_filtering": True}
    def get(self, key, default=None):
        return {"recall_engine.top_k": 5, "recall_engine.auto_remove_injected": True,
                "recall_engine.injection_method": "extra_user_content"}.get(key, default)


class Cache:
    enabled = True
    def __init__(self, values=None): self.values = values or {}
    def lookup(self, key): return self.values.get(key)


def context(provider, persona="你是群聊机器人。自然交流，准确理解用户意图。", rows=None, memories=None):
    ctx = SimpleNamespace(
        persona_manager=SimpleNamespace(get_default_persona_v3=AsyncMock(return_value={"prompt": persona, "name": "测试人格"})),
        get_using_provider=lambda *a: provider, get_provider_by_id=lambda *a: provider,
        get_llm_tool_manager=lambda: None,
        message_history_manager=SimpleNamespace(get=AsyncMock(return_value=rows or [])),
    )
    engine = SimpleNamespace(search_memories=AsyncMock(return_value=memories or []))
    utils = SimpleNamespace(get_event_message_str=AsyncMock(side_effect=lambda event:event.text))
    recall = recall_module.MemoryRecall(ctx, Config(), engine, SimpleNamespace(), utils, adapter_module.InjectionAdapter())
    handler = SimpleNamespace(search_memory_for_understanding=recall.search_for_understanding)
    ctx.get_registered_star = lambda name: SimpleNamespace(activated=True, star_cls=SimpleNamespace(event_handler=handler)) if name=="astrbot_plugin_livingmemory" else None
    return ctx, recall, engine


async def build_final_request(ctx, event, formatted, recall, history=None, group_main=None, reply_handler=None):
    kwargs = await (reply_handler or reply.ReplyHandler).generate_reply(event, ctx, formatted, "", history_messages=history or [])
    if not isinstance(kwargs, dict):
        return kwargs
    req = ProviderRequest(prompt=kwargs["prompt"], system_prompt=kwargs["system_prompt"],
                          contexts=kwargs["contexts"], image_urls=kwargs["image_urls"])
    await recall.handle_memory_recall(event, req)
    cls = group_main or main.ChatPlus
    plugin = cls.__new__(cls)
    plugin.context, plugin.debug_mode, plugin.enable_image_processing = ctx, False, False
    await plugin.on_llm_request(event, req)
    support = support_module.WhaleSupport.__new__(support_module.WhaleSupport)
    support.supported = lambda event: True
    support.get_kv_data = AsyncMock(return_value={})
    await support.identity(event, req)
    import json
    stickers = stickers_module.WhaleStickers.__new__(stickers_module.WhaleStickers)
    stickers.catalog = json.loads((ROOT / "plugins/astrbot_plugin_whale_stickers/catalog.json").read_text(encoding="utf-8"))
    stickers.battle = stickers_module.Battle()
    stickers.private_battle = stickers_module.Battle()
    stickers.supported = lambda event: True
    stickers.refresh_collected = lambda event: {row["id"]:row for row in stickers.catalog[:8]}
    await stickers.inject(event, req)
    return req


def api_messages(req):
    messages = [{"role":"system", "content":req.system_prompt}] if req.system_prompt else []
    messages.extend(req.contexts or [])
    prompt = req.prompt or ""
    for part in req.extra_user_content_parts or []:
        if isinstance(part, TextPart): prompt += "\n" + part.text
        else: raise AssertionError("Replay must explicitly support every final content part")
    messages.append({"role":"user", "content":prompt})
    return messages
