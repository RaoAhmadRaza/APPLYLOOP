"""The one normaliser the zero-fabrication chain compares with.

§3.3 is enforced at two joins, and they are the same comparison seen from both ends:
M3 stores a claim only if it appears in the résumé, and M5 ships a bullet only if it
appears in the claim. If the two ends squash differently the guarantee breaks in the
middle, silently — M5 calling a bullet traceable against text M3 would have rejected,
or M5 stripping a bullet that is a verbatim quote of a stored claim.

`vault.py` said, correctly, that a four-line helper is not worth a shared package. It
becomes worth a shared *module* the moment a second stage has to agree with it exactly.
This file sits beside `regions.py` and `seniority.py` for the reason they do: worker
top-level, stage-neutral, importable by both without crossing §3.1.

`scraping/dedupe.py` keeps its own copy deliberately. It squashes company names to
build a dedupe key — the same transform for an unrelated purpose, and sharing it would
couple a scraping decision to a résumé guarantee.
"""

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def squash(value: str) -> str:
    """Lowercase, strip accents, drop everything that is not a letter or a digit.

    The dumbest transform that survives formatting noise without admitting a paraphrase.
    It has to be dumb in both directions: strict enough that "reduced latency by 76%"
    fails against a résumé where nobody wrote 76, loose enough that a line break
    markitdown inserted mid-bullet, a non-breaking space, or an accent encoded two ways
    cannot reject a true claim.

    It also drops the unmapped glyphs a PDF renderer emits — reportlab's `(cid:127)`
    bullet, a smart quote, an en dash — which is why comparison happens here rather than
    on the raw extracted text.
    """
    folded = unicodedata.normalize("NFKD", value.lower())
    stripped = "".join(char for char in folded if not unicodedata.combining(char))
    return _NON_ALNUM.sub("", stripped)
