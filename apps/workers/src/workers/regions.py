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
    (
        "GB",
        (
            "united kingdom",
            "uk",
            "u.k.",
            "britain",
            "british",
            "england",
            "london",
            "manchester",
            "edinburgh",
            "bristol",
            "leeds",
            "glasgow",
        ),
    ),
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
    ("ES", ("spain", "spanish", "madrid", "barcelona")),
    ("CH", ("switzerland", "swiss", "zurich")),
    ("RO", ("romania", "romanian", "bucharest")),
    ("PT", ("portugal", "portuguese", "lisbon")),
    ("EG", ("egypt", "egyptian", "cairo")),
    ("AR", ("argentina", "buenos aires")),
    ("UY", ("uruguay", "montevideo")),
    ("CO", ("colombia", "bogotá", "bogota")),
    ("VN", ("vietnam", "ho chi minh", "hanoi")),
    ("JP", ("japan", "tokyo")),
    ("KR", ("south korea", "seoul")),
    ("ZA", ("south africa", "cape town", "johannesburg")),
    ("IL", ("israel", "tel aviv")),
    ("SE", ("sweden", "stockholm")),
)

# US cities and the state-suffix form the pool writes them in. A separate pass because
# the country table cannot carry every city, and a GB-authorised profile drawing "San
# Francisco" is what a country-only table does — measured on the first extension draw.
_US_PLACES: tuple[str, ...] = (
    "san francisco",
    "new york",
    "seattle",
    "austin",
    "boston",
    "chicago",
    "los angeles",
    "denver",
    "atlanta",
    "portland",
    "starbase",
    "nashville",
    "houston",
    "mountain view",
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
    found = {
        token
        for token, words in REGION_WORDS
        for word in words
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", lowered)
    }
    if any(city in lowered for city in _US_PLACES) or re.search(
        r",\s*(?:ca|ny|tx|wa|ma|il|co|ga|or|tn|pa|va|az|nc|fl|oh)\b", lowered
    ):
        found.add("US")
    return found


# Above this many distinct regions, a posting is listing continents rather than scoping
# itself. "Americas, Europe, Asia, Africa, Oceania" names regions and excludes nobody.
_BREADTH_IS_GLOBAL = 3

# Continent names, which the region table deliberately does not carry — they are not
# countries and no one is authorised in "Asia". They matter only for breadth: a posting
# naming two or more is describing reach, not scope. Counted separately because only
# "Europe" overlaps the region table, so a continent list would otherwise score 1.
_CONTINENTS: tuple[str, ...] = (
    "americas",
    "north america",
    "latin america",
    "south america",
    "asia",
    "africa",
    "oceania",
    "europe",
    "emea",
    "apac",
)
_CONTINENTS_IS_GLOBAL = 2


def is_open_to_the_world(text: str, named: set[str]) -> bool:
    """Whether a location string excludes nobody, given what it named.

    Two ways to be open, and the order matters. A string that names a region is scoped
    **even when it says "remote"** — "Remote, United States" is the most common location
    in the pool and reading it as worldwide would disable this check entirely. A string
    that names several regions is listing continents, not scoping.
    """
    lowered = text.lower()
    if len(named) >= _BREADTH_IS_GLOBAL:
        return True
    if sum(continent in lowered for continent in _CONTINENTS) >= _CONTINENTS_IS_GLOBAL:
        return True
    return not named and any(phrase in lowered for phrase in GLOBAL_PHRASES)


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


# UTC offsets a region spans, as (earliest, latest) in hours. Ranges rather than points
# because a country is not a timezone: the US runs from -10 to -4 and Portland could be
# any of them from "Portland, Oregon" alone. A range is what the table can honestly say,
# and `offsets` below only answers questions the range settles either way.
#
# Partial on purpose, like `_EU_MEMBERS`: a region that is missing yields no answer and
# therefore blocks nothing.
_UTC_OFFSETS: dict[str, tuple[float, float]] = {
    "US": (-10.0, -4.0),
    "CA": (-8.0, -3.5),
    "MX": (-8.0, -5.0),
    "BR": (-5.0, -2.0),
    "AR": (-3.0, -3.0),
    "UY": (-3.0, -3.0),
    "CO": (-5.0, -5.0),
    "GB": (0.0, 1.0),
    "IE": (0.0, 1.0),
    "PT": (0.0, 1.0),
    # The bloc spans WET through EET, so it settles a question only for a window that
    # misses all of it.
    "EU": (0.0, 3.0),
    "DE": (1.0, 2.0),
    "FR": (1.0, 2.0),
    "NL": (1.0, 2.0),
    "PL": (1.0, 2.0),
    "IT": (1.0, 2.0),
    "ES": (1.0, 2.0),
    "CH": (1.0, 2.0),
    "SE": (1.0, 2.0),
    "NO": (1.0, 2.0),
    "BG": (2.0, 3.0),
    "RO": (2.0, 3.0),
    "EG": (2.0, 3.0),
    "ZA": (2.0, 2.0),
    "IL": (2.0, 3.0),
    "TR": (3.0, 3.0),
    "AE": (4.0, 4.0),
    "IN": (5.5, 5.5),
    "TH": (7.0, 7.0),
    "VN": (7.0, 7.0),
    "SG": (8.0, 8.0),
    "HK": (8.0, 8.0),
    "TW": (8.0, 8.0),
    "AU": (8.0, 11.0),
    "JP": (9.0, 9.0),
    "KR": (9.0, 9.0),
    "NZ": (12.0, 13.0),
}


def offsets(code: str) -> tuple[float, float] | None:
    """The UTC offset range a region spans, or None when the table does not say."""
    return _UTC_OFFSETS.get(code)
