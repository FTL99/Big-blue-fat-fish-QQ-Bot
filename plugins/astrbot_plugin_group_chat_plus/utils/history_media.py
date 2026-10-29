"""Keep image identity and cached descriptions when rebuilding platform history."""


def has_history_image(content):
    if isinstance(content, list):
        return any(has_history_image(part) for part in content)
    return isinstance(content, dict) and str(content.get("type", "")).lower() in {"image", "image_url", "input_image"}


def render_history_content(content, text_renderer, image_cache=None):
    if isinstance(content, list):
        return "\n".join(filter(None, (
            render_history_content(part, text_renderer, image_cache) for part in content
        )))
    if isinstance(content, dict):
        kind = str(content.get("type", "")).lower()
        if kind in {"image", "image_url", "input_image"}:
            data = content.get("data") or content.get("image_url") or content
            if isinstance(data, str):
                sources = [data]
            elif isinstance(data, dict):
                sources = [data.get(key) for key in ("file", "path", "url")]
            else:
                sources = []
            if image_cache and image_cache.enabled:
                for source in sources:
                    if isinstance(source, str) and source:
                        description = image_cache.lookup(source)
                        if description:
                            return "[图片内容: " + description + "]"
            return "[图片: 此前发送的图片，尚无可用描述]"
    return text_renderer(content)
