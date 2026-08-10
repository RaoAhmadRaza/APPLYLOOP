"""Vendor spellings of a skill the candidate already holds.

An ATS keyword-matches on exact strings. The vault says `Postgres` because the résumé
does; the posting says `PostgreSQL`; the candidate loses the keyword on a skill they
genuinely have. That is a spelling problem being scored as a capability problem.

**An alias changes spelling. It never changes possession.** Nothing here is reachable
except from a skill the vault already stores, so this cannot add a skill — which is the
only thing §3.3 forbids.

**Expansion only, one direction, and that is not a detail.** A vault spelling licenses its
fuller form and never the reverse:

    vault says `Postgres`   -> `PostgreSQL` is allowed     (this is the case §4b.2 wants)
    vault says `PostgreSQL` -> `Postgres` is NOT allowed

The second line is case `S-06` in `evals/fabrication/cases.json`, which exists to keep
exactly that refused and whose own note says why it is right on the merits: the résumé
says PostgreSQL, so the résumé should say PostgreSQL. A symmetric table would make that
committed case pass, and Part 13 rule 3 does not allow moving it.

The direction also happens to be the safe one on its own terms. Expanding an abbreviation
states something more specific than the résumé did — `K8s` really is Kubernetes.
Contracting states something *less* specific, and less specific is where a near-miss
hides: `Postgres` covers PostgreSQL and also a dozen things that are not it.

Deliberately tiny and hand-written. This is not a synonym engine and must never become
one: `AWS` and `Azure` are not aliases, and adding a pair like that would be class F2 in
BAR.md §4 wearing a lookup table. Add an entry when a real posting is measured to have
cost a real candidate a real keyword.
"""

from workers.text import squash

# squashed vault spelling -> the fuller spelling it licenses.
_EXPANSIONS: dict[str, str] = {
    "postgres": "PostgreSQL",
    "k8s": "Kubernetes",
    "js": "JavaScript",
    "ts": "TypeScript",
    "gcp": "Google Cloud Platform",
    "aws": "Amazon Web Services",
    "ci": "Continuous Integration",
    "ml": "Machine Learning",
}


def expand(value: str) -> str | None:
    """The fuller spelling this one licenses, or None.

    Whole-string only. A group line is split by `text.split_skill_line` before it reaches
    here, so `Languages: JS, Go` expands through its member `JS` and never as a whole —
    and a substring rule would let `js` inside `jsonschema` license `JavaScript`.
    """
    return _EXPANSIONS.get(squash(value))
