"""HTML -> plain text, on the standard library.

The six ATS providers disagree about description format: Greenhouse returns
HTML-escaped HTML, Recruitee returns raw HTML, and the rest return plain text. Storing
all three shapes in one column hands M4 a corpus half full of markup, so every adapter
routes its description through here.

The original markup is never lost — `jobs.raw_json` keeps the untouched payload.
"""

import html
import re
from html.parser import HTMLParser

# Tags whose content is markup or code, not prose.
_SKIP_TAGS = frozenset({"script", "style"})

# Tags that end a line of prose. Everything else is inline.
_BREAK_TAGS = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"})

_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(raw: str | None) -> str | None:
    """Collapse HTML (escaped or not) to readable plain text. `None` in, `None` out."""
    if not raw:
        return None

    # Greenhouse double-encodes: its `content` field is HTML that has itself been
    # entity-escaped, so it needs unescaping before it will even parse as markup.
    unescaped = html.unescape(raw)

    parser = _Stripper()
    parser.feed(unescaped)
    parser.close()

    text = _TRAILING_SPACE.sub("\n", parser.text())
    text = _BLANK_LINES.sub("\n\n", text).strip()
    return text or None
