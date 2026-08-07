"""Résumé bytes to Markdown.

markitdown (Microsoft, MIT) rather than calling a PDF library directly, because it
handles PDF, DOCX and plain text behind one entry point and emits **Markdown** rather
than a wall of text — headings, lists and tables survive, which is materially better
input for an extraction model than the same characters with their structure stripped.

Rejected: **PyMuPDF**, which is 8–12x faster and licensed **AGPL-3.0**. Its network
clause would require this service to be open-sourced or an Artifex licence bought. Speed
does not buy that back. markitdown's `[pdf]` extra is pdfminer.six + pdfplumber and its
`[docx]` extra is mammoth + lxml, all permissive.

**Known ceiling, and why it is survivable.** pdfminer.six has documented reading-order
problems on two-column PDFs — it interleaves the columns line by line. That degrades
extraction quality, and the live suite is where it gets measured. It cannot, however,
corrupt the vault: the model reads exactly the text `vault.py` later verifies claims
against, so a mangled layout produces a worse record, never an unverifiable one.
ponytail: revisit with a layout-aware converter if the live gate shows real two-column
résumés failing.
"""

import io

from markitdown import MarkItDown

# What the upload endpoint accepts and this module can read. Anything else is rejected
# at the boundary rather than discovered here as an empty string.
SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md"})

# markitdown builds its converter registry once and holds a magika model. Constructing
# it per résumé would reload that on every upload for no benefit; it holds no per-call
# state, so one instance is safe.
_CONVERTER = MarkItDown()


class ExtractError(RuntimeError):
    """The file could not be turned into usable text."""


def to_markdown(data: bytes, suffix: str) -> str:
    """Convert one résumé. `suffix` is the format hint, e.g. `.pdf`.

    The hint comes from the stored object key rather than from sniffing: the key is what
    `profiles.resume_url` holds, and the upload endpoint has already validated it.
    """
    if suffix not in SUFFIXES:
        raise ExtractError(f"unsupported résumé format: {suffix}")

    try:
        result = _CONVERTER.convert_stream(io.BytesIO(data), file_extension=suffix)
    except Exception as error:  # noqa: BLE001 — markitdown raises per-converter types
        raise ExtractError(f"could not read {suffix} résumé") from error

    text = (result.text_content or "").strip()
    if not text:
        # A scanned PDF with no text layer lands here. §3.7's rule about volume applies:
        # an empty extraction that silently became an empty profile would look like a
        # user with no experience rather than like a failure.
        raise ExtractError(f"{suffix} résumé produced no text")
    return text
