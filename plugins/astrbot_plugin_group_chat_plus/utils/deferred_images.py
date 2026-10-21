"""Retain filtered images locally; describe them only when building later context."""
import asyncio
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from .image_handler import ImageHandler

_TOKEN = re.compile(r"\[图片内容: 尚未识别；图片索引=([0-9a-f]{64})\]")


def _directory(owner):
    cache = getattr(owner, "image_description_cache", None)
    root = getattr(cache, "_cache_dir", None)
    if root is None:
        root = Path(get_astrbot_data_path()) / "plugin_data/astrbot_plugin_group_chat_plus/image_cache"
    return Path(root) / "deferred"


async def retain_unread_images(owner, event):
    if not owner.enable_image_processing:
        return None
    chain = event.message_obj.message
    _, _, images = ImageHandler._analyze_message(chain, owner.max_images_per_message)
    descriptions = {}
    directory = _directory(owner)
    directory.mkdir(parents=True, exist_ok=True)
    # Only our content-addressed image copies; keep at least the pending-cache lifetime.
    cutoff = time.time() - max(86400, owner.cache_manager.cache_ttl_seconds)
    for path in directory.glob("*.img"):
        if re.fullmatch(r"[0-9a-f]{64}\.img", path.name) and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
    for index, image in enumerate(images):
        try:
            source = await image.convert_to_file_path()
            cache = owner.image_description_cache
            known = cache.lookup(source) if cache and cache.enabled else None
            if known:
                descriptions[index] = known
                continue
            if owner.image_to_text_scope != "all" or Path(source).stat().st_size > 32 * 1024 * 1024:
                continue
            data = await asyncio.to_thread(Path(source).read_bytes)
            if not data or len(data) > 32 * 1024 * 1024:
                continue
            key = hashlib.sha256(data).hexdigest()
            target = directory / (key + ".img")
            if not target.exists():
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
                    temporary = Path(output.name)
                    output.write(data)
                os.replace(temporary, target)
            target.touch()
            descriptions[index] = "尚未识别；图片索引=" + key
        except Exception as error:
            logger.warning(f"[图片回看] 保留图片失败: {type(error).__name__}")
    if not descriptions:
        return None
    return ImageHandler._render_message_chain(
        chain, self_id=str(event.get_self_id()), image_descriptions=descriptions
    ).strip()


async def resolve_unread_texts(owner, event, texts):
    keys = list(dict.fromkeys(key for text in texts for key in _TOKEN.findall(text or "")))
    if not keys or not owner.enable_image_processing or not owner.image_to_text_provider_id:
        return texts
    if not hasattr(owner, "_unread_image_lock"):
        # ponytail: one small group's queue; use per-group locks if this deployment grows.
        owner._unread_image_lock = asyncio.Lock()
    resolved = {}
    async with owner._unread_image_lock:
        provider = owner.context.get_provider_by_id(owner.image_to_text_provider_id)
        if not provider:
            return texts
        deadline = asyncio.get_running_loop().time() + owner.image_to_text_timeout
        cache = owner.image_description_cache
        for key in keys[-owner.max_images_per_message:]:
            path = _directory(owner) / (key + ".img")
            description = cache.lookup(str(path)) if cache and cache.enabled else None
            if not description and path.is_file():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    from .image_identity import describe_image
                    description = await asyncio.wait_for(describe_image(
                        provider, str(path), owner.image_to_text_prompt,
                        getattr(event, "session_id", ""),
                    ), timeout=remaining)
                    if description and cache and cache.enabled:
                        cache.save(str(path), description)
                except Exception as error:
                    logger.warning(f"[图片回看] 补识别失败，保留待识别状态: {type(error).__name__}")
            if description:
                resolved[key] = description
        if resolved:
            logger.info(f"[图片回看] 已补充 {len(resolved)} 张历史图片的描述")
    return [_TOKEN.sub(lambda m: "[图片内容: " + resolved[m[1]] + "]" if m[1] in resolved else m[0], text or "")
            for text in texts]


async def resolve_unread_context(owner, event, chat_id, history):
    cached = owner.cache_manager.pending_messages_cache.get(chat_id, [])
    records = [row for row in cached if isinstance(row.get("content"), str)]
    messages = [msg for msg in history if isinstance(getattr(msg, "message_str", None), str)]
    texts = [msg.message_str for msg in messages] + [row["content"] for row in records]
    results = await resolve_unread_texts(owner, event, texts)
    for msg, text in zip(messages, results):
        msg.message_str = text
    for row, text in zip(records, results[len(messages):]):
        row["content"] = text
