"""Regression: admitted requests, video identity, follow-ups and final recall boundary."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import runtime_harness as h
from candidate_gcp.utils import bilibili_reader as b

A, B = "BV1EdHV6ZEqL", "BV1nunz6AEDP"
def url(bv, part=1): return f"https://www.bilibili.com/video/{bv}?p={part}"


class IsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        async def read(link):
            _, identity, part = b._target(link)
            return {"bvid": identity, "cid": (100 if identity == A else 200)+part, "part": part,
                    "title": "同名系列", "status": "content_available", "subtitles": [
                        {"from": 1, "to": 2, "text": "钢厂火灾阿祖" if identity == A else "姐姐在车站等弟弟"}]}
        self.reader = SimpleNamespace(directory=Path(self.tmp.name), read=AsyncMock(side_effect=read))
        self.ctx = SimpleNamespace(_group_chat_plus_bilibili_reader=self.reader)

    async def enrich(self, question, history="", sender="alice", group="test-group"):
        event = h.Event(question)
        event.get_sender_id = lambda: sender
        event.get_group_id = lambda: group
        return event, await b.enrich_bilibili_context(self.ctx, event, history+"\n当前消息："+question)

    async def test_new_video_does_not_receive_old_video_or_old_bot_claim(self):
        history = f"[消息ID:old] [发言身份:机器人] {url(A)} 钢厂火灾阿祖，人民服务"
        event, text = await self.enrich("总结一下 " + url(B), history)
        self.assertIn("姐姐在车站等弟弟", text)
        self.assertNotIn("钢厂火灾", text)
        self.assertNotIn(A, text)
        self.assertEqual(event.get_extra("_bilibili_focus")["links"], [url(B)])

    async def test_current_read_failure_cannot_fall_back_to_old_video(self):
        self.reader.read.return_value = {"status": "unavailable", "reason": "timeout"}
        self.reader.read.side_effect = None
        _, text = await self.enrich("总结 " + url(B), f"旧视频 {url(A)} 阿祖救火")
        self.assertIn("unavailable", text)
        self.assertNotIn("阿祖", text)
        self.assertNotIn(A, text)

    async def test_followup_binds_original_caption_without_history_commit(self):
        await self.enrich("什么时候我也能演一集 " + url(B))
        _, text = await self.enrich("你看完没有啊", f"旧历史只有 {url(A)}")
        self.assertIn(B, text)
        self.assertIn("什么时候我也能演一集", text)
        self.assertNotIn(A, text)

    async def test_focus_survives_reader_restart(self):
        await self.enrich("总结 " + url(B))
        self.reader = SimpleNamespace(directory=Path(self.tmp.name), read=self.reader.read)
        self.ctx._group_chat_plus_bilibili_reader = self.reader
        _, text = await self.enrich("看完没有")
        self.assertIn(B, text)

    async def test_a_b_a_and_same_bvid_parts_remain_distinct(self):
        for bv, part in ((A, 1), (B, 1), (A, 2), (A, 1)):
            await self.enrich("看一下 " + url(bv, part))
            followup, text = await self.enrich("看完没有")
            self.assertEqual(followup.get_extra("_bilibili_focus")["links"], [url(bv, part)])
            self.assertIn(f'"part": {part}', text)

    async def test_explicit_comparison_keeps_both_separate(self):
        _, text = await self.enrich(f"比较 {url(A)} 和 {url(B)}")
        self.assertIn(A, text)
        self.assertIn(B, text)
        records = json.loads(text.splitlines()[1])["videos"]
        self.assertEqual(len(records), 2)
        self.assertNotEqual(records[0]["reference"], records[1]["reference"])

    async def test_other_sender_and_group_never_inherit_focus(self):
        await self.enrich("总结 " + url(B))
        for sender, group in (("bob", "test-group"), ("alice", "other-group")):
            _, text = await self.enrich("看完没有", sender=sender, group=group)
            self.assertIn("target_unresolved", text)
            self.assertNotIn(B, text)

    async def test_expired_and_ambiguous_previous_target_require_link(self):
        await self.enrich("总结 " + url(B))
        _, text = await self.enrich("总结上一个视频")
        self.assertIn("target_unresolved", text)
        with patch.object(b.time, "time", return_value=b.time.time()+1801):
            _, text = await self.enrich("看完没有")
        self.assertIn("target_unresolved", text)

    async def test_ordinary_chat_is_not_enriched_from_history_links(self):
        original = "历史 " + url(A) + "\n当前消息：晚饭吃什么"
        event = h.Event("晚饭吃什么")
        self.assertEqual(await b.enrich_bilibili_context(self.ctx, event, original), original)
        self.reader.read.assert_not_awaited()
        event.text = "总结这段话"
        self.assertEqual(await b.enrich_bilibili_context(self.ctx, event, original), original)

    async def test_video_preferences_question_keeps_normal_memory_path(self):
        await self.enrich("总结 " + url(B))
        event = h.Event("你还记得我喜欢什么视频吗")
        original = "正常多人上下文，不是新的看片请求"
        self.assertEqual(await b.enrich_bilibili_context(self.ctx, event, original), original)
        self.assertIsNone(event.get_extra("_bilibili_focus"))

    async def test_older_completion_does_not_replace_newer_focus(self):
        gate = asyncio.Event()
        old_read = self.reader.read.side_effect
        async def read(link):
            if A in link: await gate.wait()
            return await old_read(link)
        self.reader.read.side_effect = read
        old = h.Event(url(A))
        old.send = AsyncMock()
        await b.start_bilibili_read(self.ctx, old)
        await self.enrich("看 " + url(B))
        gate.set()
        await old.get_extra("_bilibili_read_task")
        # Re-enriching the old event must also leave the newer active request intact.
        await b.enrich_bilibili_context(self.ctx, old, old.text)
        event, _ = await self.enrich("看完没有")
        self.assertEqual(event.get_extra("_bilibili_focus")["links"], [url(B)])

    async def test_own_ack_is_not_other_plugin_final_reply(self):
        event = h.Event(url(B))
        async def send(_): event._has_send_oper = True  # Real AstrMessageEvent.send side effect.
        event.send = AsyncMock(side_effect=send)
        await b.start_bilibili_read(self.ctx, event)
        self.assertTrue(event._has_send_oper)
        self.assertFalse(h.reply.ReplyHandler.check_if_already_replied(event))
        await event.get_extra("_bilibili_read_task")
        other = h.Event(url(A))
        other._has_send_oper = True
        other.send = AsyncMock()
        await b.start_bilibili_read(self.ctx, other)
        other.send.assert_not_awaited()
        self.assertTrue(h.reply.ReplyHandler.check_if_already_replied(other))

    async def test_real_main_flow_reaches_content_processing_after_own_ack(self):
        owner = h.main.ChatPlus.__new__(h.main.ChatPlus)
        owner.context, owner.debug_mode = self.ctx, False
        owner.frequency_adjuster_enabled = owner.proactive_enabled = owner.enable_emoji_filter = False
        owner.enable_group_wait_window, owner.pending_cache_max_count = True, 20
        owner.group_wait_window_at_mode = owner.group_wait_window_keyword_mode = "wait"
        owner.group_wait_window_timeout_ms, owner.group_wait_window_max_users = 1, 5
        owner._group_wait_window_max_extra, owner._group_wait_window_counter = 3, 0
        owner._group_wait_window_lock, owner._group_wait_windows = asyncio.Lock(), {}
        owner._perform_initial_checks = AsyncMock(return_value=(True, "aiocqhttp", False, "test-group"))
        owner._next_group_message_seq = lambda _: 1
        owner._check_message_triggers = AsyncMock(return_value=(True, False, ""))
        owner._check_poke_message = AsyncMock(return_value={})
        owner._check_mention_others = AsyncMock(return_value=None)
        owner._maybe_intercept_for_wait_window = AsyncMock(return_value=False)
        owner._check_probability_before_processing = AsyncMock(return_value=True)
        owner._process_message_content = AsyncMock(return_value=(False,))
        event = h.Event(url(B))
        async def send(_): event._has_send_oper = True
        event.send = AsyncMock(side_effect=send)
        _ = [result async for result in owner._process_message(event)]
        owner._process_message_content.assert_awaited_once()
        event.send.assert_awaited_once()
        await event.get_extra("_bilibili_read_task")

    async def test_video_final_request_does_not_recall_old_summaries_even_if_understanding_failed(self):
        provider = SimpleNamespace(text_chat=AsyncMock(side_effect=TimeoutError()))
        ctx, recall, engine = h.context(provider)
        ctx._group_chat_plus_bilibili_reader = self.reader
        event = h.Event("总结 " + url(B))
        event.set_extra("_group_chat_plus_semantic_checked", True)
        with patch.object(h.recall_module, "is_session_enabled", AsyncMock(return_value=True)):
            req = await h.build_final_request(ctx, event, f"旧视频 {url(A)} 阿祖救火", recall)
        final = json.dumps(h.api_messages(req), ensure_ascii=False)
        engine.search_memories.assert_not_awaited()
        self.assertNotIn("阿祖救火", final)
        self.assertIn("姐姐在车站等弟弟", final)


if __name__ == "__main__": unittest.main()
