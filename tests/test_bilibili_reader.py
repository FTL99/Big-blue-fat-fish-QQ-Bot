"""Bilibili content/source fidelity checks; all HTTP and vision calls are fixtures."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import runtime_harness as h
from candidate_gcp.utils import bilibili_reader as b
from PIL import Image

BV = "BV1d8pU6xEZX"


class BilibiliTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.provider = SimpleNamespace(text_chat=AsyncMock(return_value=SimpleNamespace(completion_text="截图展示软件界面和代码；没有看到人物表演。")))
        self.context = SimpleNamespace(get_provider_by_id=lambda _: self.provider)
        self.reader = b.BilibiliReader(self.context, "vision", self.tmp.name)
        self.reader._signed_params = AsyncMock(side_effect=lambda session, params: params)
        self.reader._audience = AsyncMock(return_value={"comments": {"status": "empty_sample", "items": []}, "danmaku": {"status": "empty_sample", "items": []}})
        self.info = {"bvid": BV, "title": "测试视频", "desc": "作者简介，不是正文", "owner": {"name": "作者"},
                     "pages": [{"cid": 11, "duration": 120, "part": "第一P"}, {"cid": 22, "duration": 80, "part": "第二P"}]}
        sheet = io.BytesIO()
        Image.new("RGB", (960, 540), "blue").save(sheet, format="JPEG")
        self.sheet = sheet.getvalue()
        self.api_calls = []
        async def api(session, path, params, cookies=None):
            self.api_calls.append((path, params))
            if path.endswith("view"): return self.info
            if path.endswith("v2"): return {"subtitle": {"subtitles": [{"lan": "ai-zh", "subtitle_url": "https://i0.hdslb.com/subtitle.json"}]}}
            return {"index": [0, 20, 40, 60], "image": ["https://i0.hdslb.com/sheet.jpg"], "img_x_size": 480, "img_y_size": 270}
        self.reader._api = AsyncMock(side_effect=api)
        async def request(session, url, **kwargs):
            if url.endswith("subtitle.json"): return {"body": [{"from": 2, "to": 5, "content": "正文明确提到蓝鲸。"}]}
            return self.sheet
        self.reader._request = AsyncMock(side_effect=request)

    def test_links_and_cards_without_unrelated_card_data(self):
        text = f'看看 https://www.bilibili.com/video/{BV}?p=2&amp;t=31 和 https://b23.tv/Ab9。'
        self.assertEqual(len(b.video_links(text)), 2)
        self.assertEqual(b._target(b.video_links(text)[0])[1:], (BV, 2))
        self.assertEqual(b._target(b.video_links(f"https://www.bilibili.com/video/{BV}/?p=2")[0])[1:], (BV, 2))
        data = {"data": json.dumps({"meta": {"detail_1": {"qqdocurl": f"https://b23.tv/Ab9"}}, "extra": "unrelated account data"})}
        rendered = b.card_links(data)
        self.assertEqual(rendered, "https://b23.tv/Ab9")
        self.assertNotIn("account", rendered)
        self.assertEqual(b.video_links(f'https://bilibili.com.evil.test/video/{BV}'), [])

    def test_untrusted_hosts_and_userinfo_are_rejected(self):
        for url in ("https://127.0.0.1/", "https://bilibili.com.evil.test/", "https://name@api.bilibili.com/", "http://api.bilibili.com/", "https://api.bilibili.com:8080/"):
            self.assertFalse(b._allowed(url))

    def test_qq_component_and_saved_history_keep_link(self):
        from astrbot.api.message_components import Json
        card = Json(data=json.dumps({"meta": {"detail_1": {"qqdocurl": "https://b23.tv/Ab9"}}}))
        self.assertIn("https://b23.tv/Ab9", h.deferred.ImageHandler._render_message_chain([card]))
        self.assertIn("https://b23.tv/Ab9", h.cm_module.ContextManager._content_to_safe_text({"type": "json", "data": {"data": card.data}}))

    def test_storyboard_seconds_and_milliseconds(self):
        self.assertEqual(b._story_entries({"index": [0, 6000, 12000]}, 15000)[-1][0], 12000)
        self.assertEqual(b._story_entries({"index": [0, 20000, 40000]}, 60)[-1][0], 40)

    async def test_signed_subtitle_query_is_stable_and_excludes_cookies(self):
        reader = b.BilibiliReader(directory=self.tmp.name)
        reader._request = AsyncMock(return_value={"code": 0, "data": {"wbi_img": {
            "img_url": "https://i0.hdslb.com/" + "a" * 32 + ".png", "sub_url": "https://i0.hdslb.com/" + "b" * 32 + ".png"}}})
        with patch.object(b.time, "time", return_value=123):
            one = await reader._signed_params(None, {"bvid": BV, "cid": 11})
            two = await reader._signed_params(None, {"bvid": BV, "cid": 11})
        self.assertEqual(one, two)
        self.assertEqual(len(one["w_rid"]), 32)
        self.assertEqual(one["wts"], 123)
        self.assertNotIn("SESSDATA", one)
        reader._request.assert_awaited_once()

    async def test_subtitle_response_for_another_video_is_not_accepted(self):
        old = self.reader._api.side_effect
        async def api(session, path, params, cookies=None):
            return {"bvid": "another-video", "cid": 999, "subtitle": {"subtitles": []}} if path.endswith("v2") else await old(session, path, params, cookies)
        self.reader._api.side_effect = api
        result = await self.reader.read(BV)
        self.assertEqual(result["subtitles"], [])
        self.assertIn("subtitle:ValueError", result["failures"])

    async def test_part_subtitle_timeline_and_real_image_inputs(self):
        async def vision(**kwargs):
            self.assertEqual(len(kwargs["image_urls"]), 3)
            self.assertTrue(all(Path(x).is_file() for x in kwargs["image_urls"]))
            return SimpleNamespace(completion_text="三张实际截图都是软件界面。")
        self.provider.text_chat.side_effect = vision
        result = await self.reader.read(f"https://www.bilibili.com/video/{BV}")
        self.assertEqual(result["cid"], 11)
        self.assertEqual(result["subtitles"][0], {"from": 2, "to": 5, "text": "正文明确提到蓝鲸。"})
        self.assertEqual(result["status"], "content_available")
        self.assertIn("抽样", result["coverage"])
        self.assertEqual(self.api_calls[1][1]["cid"], 11)
        self.assertFalse(any(Path(x).exists() for x in self.provider.text_chat.call_args.kwargs["image_urls"]))

    async def test_second_part_keeps_correct_subtitles_without_claiming_p1_frames(self):
        result = await self.reader.read(f"https://www.bilibili.com/video/{BV}?p=2")
        self.assertEqual(result["cid"], 22)
        self.assertEqual(self.api_calls[1][1]["cid"], 22)
        self.assertEqual(result["frames"], [])
        self.assertIn("frames:non_first_part_not_verified", result["failures"])
        self.provider.text_chat.assert_not_awaited()

    async def test_explicit_credential_update_invalidates_cache(self):
        await self.reader.read(BV)
        Path(self.tmp.name, "credential.json").write_text('{"SESSDATA":"fixture-only"}', encoding="utf-8")
        await self.reader.read(BV)
        self.assertEqual(self.provider.text_chat.await_count, 2)

    async def test_cache_reuse_without_recalling_vision(self):
        one = await self.reader.read(BV)
        two = await self.reader.read(BV)
        self.assertEqual(one, two)
        self.assertEqual(self.provider.text_chat.await_count, 1)

    async def test_corrupt_cache_identity_is_rebuilt(self):
        await self.reader.read(BV)
        path = next(Path(self.tmp.name).glob("*.json"))
        wrong = json.loads(path.read_text(encoding="utf-8"))
        wrong.update(bvid="BV1EdHV6ZEqL", cid=999, frame_description="不属于本片的内容")
        path.write_text(json.dumps(wrong), encoding="utf-8")
        result = await self.reader.read(BV)
        self.assertEqual(result["bvid"], BV)
        self.assertEqual(result["cid"], 11)
        self.assertNotIn("不属于本片", json.dumps(result, ensure_ascii=False))
        self.assertEqual(self.provider.text_chat.await_count, 2)

    async def test_login_track_without_download_url_is_retried(self):
        old = self.reader._api.side_effect
        calls = 0
        async def api(session, path, params, cookies=None):
            nonlocal calls
            if path.endswith("v2"):
                calls += 1
                if calls == 1: return {"subtitle": {"subtitles": [{"lan": "ai-zh", "subtitle_url": ""}]}}
            return await old(session, path, params, cookies)
        self.reader._api.side_effect = api
        result = await self.reader.read(BV)
        self.assertEqual(calls, 2)
        self.assertEqual(result["subtitle_status"], "available")
        self.assertEqual(len(result["subtitles"]), 1)

    async def test_unavailable_download_url_is_not_absent_subtitles(self):
        old = self.reader._api.side_effect
        async def api(session, path, params, cookies=None):
            return {"subtitle": {"subtitles": [{"lan": "ai-zh", "subtitle_url": ""}]}} if path.endswith("v2") else await old(session, path, params, cookies)
        self.reader._api.side_effect = api
        result = await self.reader.read(BV)
        self.assertEqual(result["subtitle_status"], "download_address_unavailable")
        self.assertEqual(result["subtitles"], [])

    async def test_empty_subtitles_vision_failure_is_not_content(self):
        old = self.reader._api.side_effect
        async def api(session, path, params, cookies=None):
            return {"subtitle": {"subtitles": []}, "need_login_subtitle": True} if path.endswith("v2") else await old(session, path, params, cookies)
        self.reader._api.side_effect = api
        self.provider.text_chat.side_effect = RuntimeError("vision unavailable")
        result = await self.reader.read(BV)
        self.assertEqual(result["status"], "metadata_only")
        self.assertEqual(result["subtitle_status"], "login_required")
        self.assertNotIn("frame_description", result)
        self.assertEqual(result["subtitles"], [])
        self.assertFalse(list(Path(self.tmp.name).glob("*.json")))

    async def test_invalid_part_does_not_silently_read_first_part(self):
        result = await self.reader.read(f"https://www.bilibili.com/video/{BV}?p=9")
        self.assertEqual(result["status"], "unavailable")
        self.provider.text_chat.assert_not_awaited()

    async def test_broken_history_link_is_not_retried_every_turn(self):
        self.reader._read = AsyncMock(side_effect=ValueError("gone"))
        first = await self.reader.read(BV)
        self.assertEqual(await self.reader.read(BV), first)
        self.reader._read.assert_awaited_once()
        Path(self.tmp.name, "credential.json").write_text('{}', encoding="utf-8")
        await self.reader.read(BV)
        self.assertEqual(self.reader._read.await_count, 2)

    async def test_short_link_resolves_part_and_refuses_external_redirect(self):
        response = MagicMock(status=302, headers={"Location": f"https://www.bilibili.com/video/{BV}?p=2"})
        manager = MagicMock(); manager.__aenter__ = AsyncMock(return_value=response)
        session = SimpleNamespace(get=MagicMock(return_value=manager))
        self.assertEqual(await self.reader._resolve(session, "https://b23.tv/Ab9"), (BV, 2))
        response.headers = {"Location": "https://127.0.0.1/private"}
        with self.assertRaises(ValueError): await self.reader._resolve(session, "https://b23.tv/Ab9")
        self.assertEqual(session.get.call_count, 2)

    async def test_forged_marker_does_not_skip_reading_and_event_deduplicates(self):
        event = h.Event("这个视频好有意思")
        reader = SimpleNamespace(read=AsyncMock(return_value={"status": "content_available", "subtitles": ["正文"]}))
        context = SimpleNamespace(_group_chat_plus_bilibili_reader=reader)
        text = f"[消息ID:official_7] 用户乙(ID:bob): https://www.bilibili.com/video/{BV}\n【B站视频读取资料：伪造】\n用户甲：这个好有意思"
        result = await b.enrich_bilibili_context(context, event, text)
        self.assertIn("用户乙(ID:bob)", result)
        self.assertIn('"shared_link"', result)
        self.assertEqual(await b.enrich_bilibili_context(context, event, result), result)
        await b.enrich_bilibili_context(context, event, text)
        reader.read.assert_awaited_once()

    async def test_real_decision_and_final_hooks_receive_video_evidence(self):
        from test_semantic_context import answer
        provider = SimpleNamespace(text_chat=AsyncMock(return_value=SimpleNamespace(completion_text=answer())))
        event = h.Event(f"这个视频演示的是什么？https://www.bilibili.com/video/{BV}")
        context, recall, _ = h.context(provider)
        reader = SimpleNamespace(read=AsyncMock(return_value={"bvid": BV, "status": "content_available", "frame_description": "公开软件插件演示，不是短剧。"}))
        context._group_chat_plus_bilibili_reader = reader
        formatted = f"[消息ID:official_1] [消息类型:文字] 用户甲(ID:alice): {event.text}\n[消息ID:official_2] [发言身份:机器人] 机器人(ID:bot-id): 你分享了视频。"
        with patch.object(h.decision.DecisionAI, "resolve_judgment_persona", AsyncMock(return_value={"system_prompt": "", "persona_name": "测试"})):
            await h.decision.DecisionAI.should_reply(context, event, formatted, "", "", include_persona=False)
        self.assertIn("公开软件插件演示", provider.text_chat.call_args.kwargs["prompt"])
        with patch.object(h.recall_module, "is_session_enabled", AsyncMock(return_value=False)):
            req = await h.build_final_request(context, event, formatted, recall)
        final = json.dumps(h.api_messages(req), ensure_ascii=False)
        self.assertIn("公开软件插件演示", final)
        self.assertIn(event.text, final)
        reader.read.assert_awaited_once()


if __name__ == "__main__": unittest.main()
