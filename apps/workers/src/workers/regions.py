"""Place names to region tokens. Worker infrastructure, not a stage.

Here rather than in `profiles/derive.py` for the reason `seniority.py` is here: §3.1
forbids `workers/matching/` importing `workers/profiles/`, and both need this table —
profiles to derive where a candidate may work, matching to decide where a job is. A
second copy would drift, and the two copies disagreeing means a user is shown a job they
cannot take, or hidden from one they can.

ISO-3166 alpha-2, plus `EU` as a bloc. Deliberately short: these are the places the pool
actually names. **An unmatched place yields no token rather than a wrong one**, which is
the safe direction on both sides — an unknown location never blocks, and unknown
authorisation never authorises.
"""

import re

REGION_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("US", ("united states", "u.s.", "usa", "us", "american", "america")),
    ("GB", ("united kingdom", "uk", "u.k.", "britain", "british", "england", "london")),
    ("EU", ("european union", "eu", "eea", "european", "europe")),
    ("CA", ("canada", "canadian")),
    ("AU", ("australia", "australian")),
    ("NZ", ("new zealand",)),
    ("IN", ("india", "indian")),
    ("DE", ("germany", "german", "berlin", "munich")),
    ("FR", ("france", "french", "paris")),
    ("NL", ("netherlands", "dutch", "amsterdam")),
    ("IE", ("ireland", "irish", "dublin")),
    ("PL", ("poland", "polish", "warsaw", "kraków", "krakow")),
    ("SG", ("singapore",)),
    ("MX", ("mexico", "mexican")),
    ("BR", ("brazil", "brasil", "brazilian")),
    ("BG", ("bulgaria", "bulgarian", "sofia")),
    ("TR", ("turkey", "türkiye", "turkish")),
    ("TW", ("taiwan", "taipei")),
    ("HK", ("hong kong",)),
    ("TH", ("thailand", "bangkok")),
    ("AE", ("uae", "dubai")),
    ("NO", ("norway", "norwegian")),
    ("IT", ("italy", "italian")),
)

# Countries inside the EU bloc, so "Poland" satisfies an "EU" authorisation. Only the
# ones this table names — a partial list is honest here, because a missing member yields
# no match and therefore no block.
_EU_MEMBERS = frozenset({"DE", "FR", "NL", "IE", "PL", "IT"})

# A posting that says any of these is not scoped to a country at all. Checked before the
# region words, because "Anywhere in the World" contains no country and a naive lookup
# would return an empty set — indistinguishable from an unparseable location.
GLOBAL_PHRASES: tuple[str, ...] = (
    "anywhere in the world",
    "worldwide",
    "work from anywhere",
    "fully remote",
    "global",
    "remote",
)


def named_in(text: str) -> set[str]:
    """Region tokens named in one string, matched on word boundaries.

    Boundaries because the short forms are substrings of ordinary words: `us` appears in
    "status" and "because", `eu` in "european" and "euro", `uk` in "Ukraine". A substring
    match would name a region the text never mentioned.
    """
    lowered = text.lower()
    return {
        token
        for token, words in REGION_WORDS
        for word in words
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", lowered)
    }


def is_global(text: str) -> bool:
    """Whether a location string declines to name a place at all."""
    return any(phrase in text.lower() for phrase in GLOBAL_PHRASES)


def covers(authorised: set[str], wanted: set[str]) -> bool:
    """Whether an authorisation set reaches a job's regions.

    `EU` on either side expands: a candidate authorised in the EU can take a role in
    Poland, and a role scoped to "Europe" is open to someone authorised in Germany.

    An empty `authorised` does **not** pass. Callers distinguish "the résumé did not say"
    (`None`, handled before reaching here) from "it said, and the answer is nowhere"
    (`{}`), and collapsing the two would silently authorise someone who needs sponsorship
    in every country they named.
    """
    if not wanted:
        return True
    expanded = set(authorised)
    if "EU" in authorised:
        expanded |= _EU_MEMBERS
    if "EU" in wanted and expanded & _EU_MEMBERS:
        return True
    return bool(expanded & wanted)
