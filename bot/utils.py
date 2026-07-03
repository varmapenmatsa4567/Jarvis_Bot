import re

from bot.state import _running_tasks

_ALLOWED_TAGS = frozenset({
    "b", "strong", "i", "em", "u", "ins",
    "s", "strike", "del",
    "a", "code", "pre",
    "span",
})


def _sanitize_html(text: str) -> str:
    def _replace_tag(m):
        full = m.group(0)
        tag = m.group(1).lower().split()[0]
        if tag in _ALLOWED_TAGS or tag.startswith("/"):
            return full
        if tag in ("p", "div", "span", "h1", "h2", "h3", "h4", "h5", "h6"):
            return ""
        if tag in ("ul", "ol", "li", "hr", "br", "blockquote"):
            return ""
        return ""
    return re.sub(r'</?(\w+)[^>]*>', _replace_tag, text)


def _parse_stop(text: str):
    m = re.match(r'^\s*stop\b[,;:.!\- ]+(.*)', text, re.IGNORECASE)
    if not m:
        m = re.match(r'^\s*stop\s*$', text, re.IGNORECASE)
    if m:
        rest = m.group(1).strip().strip(',;:.!- ').strip() if m.lastindex and m.group(1) else ""
        return True, rest
    return False, text


def _cancel_task(chat_id: int):
    task = _running_tasks.get(chat_id)
    if task and not task.done():
        task.cancel()
