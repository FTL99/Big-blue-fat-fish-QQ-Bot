"""Behavior checks using the installed AstrBot components; no live messages or LLM calls."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from astrbot.api.message_components import At, Plain, Record

SOURCE = Path(__file__).resolve().parents[1] / "plugins/astrbot_plugin_whale_support/voice.py"
spec = importlib.util.spec_from_file_location("serena_voice_under_test", SOURCE)
voice = importlib.util.module_from_spec(spec)
spec.loader.exec_module(voice)


class Event:
    def __init__(self, text, chain=None):
        self.message_obj = SimpleNamespace(message=[Plain(text)], message_id="test-voice-1")
        self.extra = {}
        self.unified_msg_origin = "qq-napcat:GroupMessage:test"
        self.result = SimpleNamespace(chain=chain or [Plain("别逞强啦，先歇一会儿。")], is_llm_result=lambda: True)

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_message_str(self):
        return "".join(x.text for x in self.message_obj.message if isinstance(x, Plain))

    def get_sender_id(self):
        return "user-1"

    def get_self_id(self):
        return "bot-1"

    def get_result(self):
        return self.result


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = SimpleNamespace(text_chat=AsyncMock())
        self.owner = SimpleNamespace(
            context=SimpleNamespace(get_using_provider_async=AsyncMock(return_value=self.provider)),
            logger=MagicMock(),
        )
        self.voice = voice.SerenaVoice(self.owner)
        self.voice.backend = SimpleNamespace(synthesize=AsyncMock(return_value=r"D:\Qwen3-TTS\outputs\test.wav"))

    def prepare(self, text, plan=None, chain=None):
        event = Event(text, chain)
        self.voice.mark(event)
        req = SimpleNamespace(system_prompt="嘴硬心软的蓝发鲸鱼女仆，关心朋友但不说教。", prompt="朋友今天很累，前文正在聊加班。", contexts=[{"role": "user", "content": "今天累坏了"}])
        self.voice.prepare(event, req)
        self.voice.finished(event, SimpleNamespace(is_chunk=False))
        self.provider.text_chat.return_value = SimpleNamespace(completion_text=json.dumps(plan or {"voice_requested": True, "instruct": "嘴上稍显嫌弃，实际关心，放轻声音、语速稍慢。"}, ensure_ascii=False))
        return event, req

    async def test_ordinary_chat_never_calls_planner_or_backend(self):
        event, req = self.prepare("晚安，明天见")
        await self.voice.decorate(event)
        self.provider.text_chat.assert_not_awaited()
        self.voice.backend.synthesize.assert_not_awaited()
        self.assertNotIn("语音能力", req.system_prompt)

    async def test_explicit_voice_uses_persona_context_and_ai_direction(self):
        event, req = self.prepare("用语音安慰我一下")
        await self.voice.decorate(event)
        self.assertFalse(event.get_extra("enable_streaming"))
        self.assertIn("语音能力", req.system_prompt)
        data = json.loads(self.provider.text_chat.call_args.kwargs["prompt"])
        self.assertIn("嘴硬心软", data["人设及本轮系统上下文"])
        self.assertIn("加班", data["本轮上下文"])
        self.assertEqual(data["当前消息原文"], "用语音安慰我一下")
        self.voice.backend.synthesize.assert_awaited_once_with("别逞强啦，先歇一会儿。", "嘴上稍显嫌弃，实际关心，放轻声音、语速稍慢。")
        self.assertIsInstance(event.result.chain[0], Record)
        self.assertEqual(event.result.chain[0].text, "别逞强啦，先歇一会儿。")

    async def test_discussion_negation_and_other_person_request_do_not_synthesize(self):
        for text in ("Qwen语音模型是怎么工作的？", "不要用语音回复我", "@张三 你发条语音给我", "有人说过用语音回复，但我只是在转述"):
            with self.subTest(text=text):
                event, _ = self.prepare(text, {"voice_requested": False, "instruct": ""})
                await self.voice.decorate(event)
                self.assertIsInstance(event.result.chain[0], Plain)
        self.voice.backend.synthesize.assert_not_awaited()

    async def test_old_request_and_quoted_request_do_not_enable_new_turn(self):
        event, _ = self.prepare("现在回答普通问题")
        event.message_obj.message.insert(0, SimpleNamespace(chain=[Plain("用语音回复我")]))
        self.voice.mark(event)
        event.set_extra("_serena_context", {"最近对话": "上一轮用语音回复我"})
        await self.voice.decorate(event)
        self.provider.text_chat.assert_not_awaited()
        self.voice.backend.synthesize.assert_not_awaited()

    async def test_intermediate_tool_text_is_not_spoken(self):
        event, _ = self.prepare("用语音告诉我查询结果")
        event.set_extra("_serena_final_ready", False)
        await self.voice.decorate(event)
        self.voice.backend.synthesize.assert_not_awaited()
        self.voice.finished(event, SimpleNamespace(is_chunk=True))
        self.assertFalse(event.get_extra("_serena_final_ready"))

    async def test_voice_is_one_message_and_preserves_nontext_components(self):
        at = At(qq="123")
        event, _ = self.prepare("语音说吧", chain=[at, Plain("第一句。"), Plain("第二句。")])
        await self.voice.decorate(event)
        self.assertIs(event.result.chain[0], at)
        self.assertEqual(len(event.result.chain), 2)
        self.assertEqual(event.result.chain[1].text, "第一句。第二句。")
        await self.voice.decorate(event)
        self.voice.backend.synthesize.assert_awaited_once()

    async def test_cleared_or_non_llm_result_does_not_start_tts(self):
        event, _ = self.prepare("语音回复")
        event.result = None
        await self.voice.decorate(event)
        event.result = SimpleNamespace(chain=[Plain("命令回复")], is_llm_result=lambda: False)
        await self.voice.decorate(event)
        self.voice.backend.synthesize.assert_not_awaited()

    async def test_existing_audio_is_not_duplicated(self):
        event, _ = self.prepare("语音回复", chain=[Record(file="already.wav")])
        await self.voice.decorate(event)
        self.provider.text_chat.assert_not_awaited()

    async def test_failed_synthesis_keeps_text_and_explains_failure(self):
        event, _ = self.prepare("用语音安慰我")
        self.voice.backend.synthesize.side_effect = TimeoutError("test")
        await self.voice.decorate(event)
        self.assertEqual(event.result.chain[0].text, "别逞强啦，先歇一会儿。")
        self.assertIn("没生成成功", event.result.chain[-1].text)
        self.assertFalse(any(isinstance(c, Record) for c in event.result.chain))

    async def test_malformed_plan_never_starts_tts(self):
        for raw in ('{"voice_requested":"true","instruct":"开心"}', '{"voice_requested":true,"instruct":""}', 'not json'):
            event, _ = self.prepare("用语音回复我")
            self.provider.text_chat.return_value = SimpleNamespace(completion_text=raw)
            await self.voice.decorate(event)
        self.voice.backend.synthesize.assert_not_awaited()

    async def test_server_is_started_once_for_simultaneous_cold_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = voice.LocalSerena(directory)
            backend.ready = AsyncMock(side_effect=[False, True, True])
            backend.process_running = MagicMock(return_value=False)
            process = SimpleNamespace(returncode=None)
            with patch.object(voice.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)) as start:
                await asyncio.gather(backend.ensure_running(), backend.ensure_running())
                start.assert_awaited_once()
                self.assertIn("--no-browser", start.call_args.args)


if __name__ == "__main__":
    unittest.main()
