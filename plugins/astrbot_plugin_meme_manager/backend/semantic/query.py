"""运行时语义查询和候选 ID 校验。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .index import EmbeddingAdapter, search_index
from .models import REVIEW_CATEGORY, parse_meme_id
from .storage import (
    file_sha256,
    load_metadata,
    safe_relative_path,
    semantic_metadata_is_complete,
)


async def search_memes_multi_pack(
    pack_contexts: list[dict[str, Any]],
    plugin_data_dir: Path | str,
    query: str,
    embedding_provider: Any,
    *,
    top_k: int = 5,
    min_score: float = 0.25,
) -> dict[str, Any]:
    """Search across multiple packs with complete semantic indexing.

    Args:
        pack_contexts: List of pack contexts, each with pack_id and pack_dir.
        plugin_data_dir: Plugin data directory containing semantic indexes.
        query: Semantic query text.
        embedding_provider: Embedding model provider used by pack indexes.
        top_k: Maximum number of candidates to return (total across all packs).
        min_score: Minimum cosine similarity score required for candidates.

    Returns:
        Search result with validated candidates from all packs.
    """
    if not str(query or "").strip():
        return {"ok": True, "candidates": [], "reason": "查询词不能为空"}

    all_candidates = []
    searched_packs = []

    for ctx in pack_contexts:
        pack_id = ctx.get("pack_id")
        pack_dir = ctx.get("pack_dir")
        if not pack_id or not pack_dir:
            continue

        metadata = load_metadata(pack_dir)
        if not metadata.get("images"):
            continue
        if not semantic_metadata_is_complete(
            pack_dir, metadata, require_embeddings=True
        ):
            continue

        candidates = await search_index(
            plugin_data_dir,
            pack_id,
            query,
            EmbeddingAdapter(embedding_provider),
            metadata,
            top_k=top_k,
            min_score=min_score,
        )

        # Tag each candidate with source pack_id for traceability.
        for item in candidates:
            item["_source_pack_id"] = pack_id
            item.pop("content_sha256", None)
            item.pop("entry_id", None)

        all_candidates.extend(candidates)
        searched_packs.append(pack_id)

    if not all_candidates:
        reason_suffix = (
            f" (已检索 {len(searched_packs)} 个表情包)" if searched_packs else ""
        )
        return {
            "ok": True,
            "candidates": [],
            "reason": f"没有找到足够匹配的表情包{reason_suffix}",
        }

    # Sort by score descending and take top_k across all packs.
    all_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    top_candidates = all_candidates[:top_k]

    # Remove score field from final output.
    for item in top_candidates:
        item.pop("score", None)

    return {
        "ok": True,
        "candidates": top_candidates,
        "max_selectable": 1,
        "_searched_packs": searched_packs,
    }


async def search_memes(
    pack_dir: Path | str,
    plugin_data_dir: Path | str,
    pack_id: str,
    query: str,
    embedding_provider: Any,
    *,
    top_k: int = 5,
    min_score: float = 0.25,
    _verified_complete: bool = False,
) -> dict[str, Any]:
    """检索已完全语义化的表情包。

    Args:
        pack_dir: 所选表情包的根目录。
        plugin_data_dir: 包含语义索引的插件数据目录。
        pack_id: 所选表情包的标识符。
        query: 语义查询文本。
        embedding_provider: 表情包索引使用的嵌入模型提供商。
        top_k: 最多返回的候选数量。
        min_score: 返回候选所需的最低余弦相似度。
        _verified_complete: 请求范围内的内部凭据，证明提示词注入前已检查同一表情包。

    Returns:
        包含已校验公开候选标识符的检索结果。
    """
    if not str(query or "").strip():
        return {"ok": True, "candidates": [], "reason": "查询词不能为空"}
    metadata = load_metadata(pack_dir)
    if not metadata.get("images"):
        return {"ok": True, "candidates": [], "reason": "资源包没有语义元数据"}
    if not _verified_complete and not semantic_metadata_is_complete(
        pack_dir, metadata, require_embeddings=True
    ):
        return {
            "ok": True,
            "candidates": [],
            "reason": "资源包尚未完成100%语义化，不能作为语义检索目标",
        }
    candidates = await search_index(
        plugin_data_dir,
        pack_id,
        query,
        EmbeddingAdapter(embedding_provider),
        metadata,
        top_k=top_k,
        min_score=min_score,
    )
    for item in candidates:
        item.pop("content_sha256", None)
        item.pop("entry_id", None)
        item.pop("score", None)
    if not candidates:
        return {"ok": True, "candidates": [], "reason": "没有找到足够匹配的表情包"}
    return {"ok": True, "candidates": candidates, "max_selectable": 1}


def candidate_records(
    pack_dir: Path | str, candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """为事件上下文补回完整语义键和内容哈希，但不把它们暴露给 LLM。"""
    metadata = load_metadata(pack_dir)
    records = []
    for candidate in candidates:
        value = str(candidate.get("id") or "")
        prefix = parse_meme_id(value)
        if not prefix:
            continue
        matches = [
            (entry_id, item)
            for entry_id, item in metadata.get("images", {}).items()
            if str(entry_id).startswith(prefix)
        ]
        if len(matches) != 1:
            continue
        entry_id, item = matches[0]
        records.append(
            {
                **candidate,
                "entry_id": entry_id,
                "content_sha256": str(item.get("content_sha256") or ""),
                "caption": item.get("caption", ""),
                "tags": item.get("tags", []),
            }
        )
    return records


def candidate_records_multi_pack(
    pack_contexts: list[dict[str, Any]], candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Restore full semantic keys and content hashes for multi-pack candidates.

    Args:
        pack_contexts: List of pack contexts with pack_id and pack_dir.
        candidates: Candidates returned by search_memes_multi_pack.

    Returns:
        Enriched candidate records with entry_id and content_sha256.
    """
    pack_dirs = {ctx["pack_id"]: ctx["pack_dir"] for ctx in pack_contexts}
    records = []

    for candidate in candidates:
        source_pack_id = candidate.get("_source_pack_id")
        if not source_pack_id or source_pack_id not in pack_dirs:
            continue

        pack_dir = pack_dirs[source_pack_id]
        metadata = load_metadata(pack_dir)
        value = str(candidate.get("id") or "")
        prefix = parse_meme_id(value)
        if not prefix:
            continue

        matches = [
            (entry_id, item)
            for entry_id, item in metadata.get("images", {}).items()
            if str(entry_id).startswith(prefix)
        ]
        if len(matches) != 1:
            continue

        entry_id, item = matches[0]
        records.append(
            {
                **candidate,
                "entry_id": entry_id,
                "content_sha256": str(item.get("content_sha256") or ""),
                "caption": item.get("caption", ""),
                "tags": item.get("tags", []),
            }
        )

    return records


def remember_candidates(event: Any, candidates: list[dict[str, Any]]) -> None:
    if hasattr(event, "set_extra"):
        existing = (
            event.get_extra("meme_manager_semantic_candidates")
            if hasattr(event, "get_extra")
            else None
        )
        candidate_map = dict(existing) if isinstance(existing, dict) else {}
        candidate_map.update(
            {str(item.get("id")): item for item in candidates if item.get("id")}
        )
        event.set_extra(
            "meme_manager_semantic_candidates",
            candidate_map,
        )


def validate_selected_id(event: Any, value: str, pack_dir: Path | str) -> Path | None:
    prefix = parse_meme_id(value)
    if not prefix:
        return None
    candidate_map = (
        event.get_extra("meme_manager_semantic_candidates")
        if hasattr(event, "get_extra")
        else None
    )
    candidate = (
        candidate_map.get(str(value).strip())
        if isinstance(candidate_map, dict)
        else None
    )
    if not isinstance(candidate, dict):
        return None
    entry_id = str(candidate.get("entry_id") or "")
    if not entry_id.startswith(prefix):
        return None
    metadata = load_metadata(pack_dir)
    record = metadata.get("images", {}).get(entry_id)
    if not isinstance(record, dict):
        return None
    if str(record.get("category") or "") == REVIEW_CATEGORY:
        return None
    path = safe_relative_path(pack_dir, record.get("relative_path", ""))
    if path is None or not path.is_file():
        return None
    try:
        if file_sha256(path) != str(record.get("content_sha256") or ""):
            return None
    except OSError:
        return None
    return path


def dumps_result(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)
