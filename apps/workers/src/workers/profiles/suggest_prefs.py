"""Suggested search preferences from a just-parsed résumé.

Deliberately not the same rules as `prompt.py`'s "copy, never summarise" — this is not
the evidence-vault path. `titles` here means "what to search for", a rephrasing of the
candidate's own experience toward a target role, not a claim shown to an employer. §3.3's
zero-fabrication rule is about documents an employer reads; a search filter the candidate
can edit or ignore before it ever saves is a different kind of output. It still must not
invent facts that aren't in the résumé (a location the person never gave, a skill they
don't have) — the boundary is "grounded rephrasing", not "verbatim".
"""

from schemas.prefs import PrefsSuggestion
from schemas.resume import ParsedResume

from workers import llm

SYSTEM = """\
You suggest job-search preferences for a candidate, based on their parsed résumé. This is \
a starting point for search filters they will review and can edit — not a document an \
employer will see.

Rules:

1. GROUND EVERYTHING IN THE RÉSUMÉ. Titles may rephrase toward a plausible target role \
(e.g. a "Staff Software Engineer" with backend/infra experience could target "Senior \
Backend Engineer" or "Staff Backend Engineer") — but every title must be a role this \
candidate's actual experience and skills support. Do not suggest a title in a field or \
seniority the résumé gives no evidence for.

2. must_have_keywords: AT MOST 1 of the candidate's OWN skills, copied from their skills \
list. Never a skill not present in the résumé. This field is an AND filter — a posting \
must mention every keyword listed to survive at all, before anything else about it is \
even considered. Two real skills together (e.g. "Flutter" and "Kotlin") already excludes \
most postings that would otherwise be a great fit, because few job descriptions happen to \
name both. Prefer zero over a second keyword when unsure.

3. locations: only locations the résumé actually states. Empty list if none given — do \
not guess a city from a name, timezone, or anything else.

4. remote_modes: infer only from explicit statements in the résumé (e.g. "fully remote", \
"open to hybrid"). If the résumé says nothing about work location preference, return an \
empty list rather than guessing.

5. exclude_keywords: only include something here if the résumé itself signals a clear \
mismatch (e.g. a purely backend engineer probably doesn't want to exclude anything — \
leave empty by default). This field is usually empty; do not invent exclusions.

Return only the JSON object described by the schema.\
"""

USER_TEMPLATE = """\
Suggest search preferences for this parsed résumé:

<parsed_resume>
{resume}
</parsed_resume>\
"""


def suggest(resume: ParsedResume) -> PrefsSuggestion:
    return llm.complete_json(
        PrefsSuggestion,
        system=SYSTEM,
        user=USER_TEMPLATE.format(resume=resume.model_dump_json()),
    )
