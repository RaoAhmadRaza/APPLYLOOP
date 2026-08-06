"""`html_to_text` is the one thing standing between six description formats and a
corpus half full of markup. M4 embeds whatever this returns."""

from workers.scraping.text import html_to_text


def test_returns_none_for_nothing() -> None:
    assert html_to_text(None) is None
    assert html_to_text("") is None
    assert html_to_text("<div></div>") is None


def test_strips_tags_and_keeps_prose() -> None:
    # Arrange / Act
    text = html_to_text("<p>Build <strong>things</strong>.</p>")

    # Assert
    assert text == "Build things."


def test_unescapes_greenhouse_double_encoding() -> None:
    """Greenhouse's `content` is HTML that has itself been entity-escaped, so it does
    not even parse as markup until unescaped once."""
    # Arrange — exactly what boards-api returns.
    raw = (
        "&lt;p&gt;Senior Engineer&lt;/p&gt;"
        "&lt;ul&gt;&lt;li&gt;Rust &amp;amp; Go&lt;/li&gt;&lt;/ul&gt;"
    )

    # Act
    text = html_to_text(raw)

    # Assert
    assert text is not None
    assert "<" not in text
    assert "Senior Engineer" in text
    assert "Rust & Go" in text


def test_drops_script_and_style_content() -> None:
    text = html_to_text("<style>.a{color:red}</style><p>Hi</p><script>x=1</script>")
    assert text == "Hi"


def test_block_tags_become_line_breaks() -> None:
    text = html_to_text("<li>One</li><li>Two</li>")
    assert text is not None
    assert [line.strip() for line in text.splitlines() if line.strip()] == ["One", "Two"]


def test_collapses_runs_of_blank_lines() -> None:
    """Nested block tags emit a break each; without collapsing, one <div><p> nest turns
    into four blank lines and the embedding budget pays for whitespace."""
    text = html_to_text("<div><div><p>A</p></div></div><div><div><p>B</p></div></div>")
    assert text == "A\n\nB"
