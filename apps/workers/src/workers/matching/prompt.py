"""The gap-analysis instructions.

Prose, versioned like code. Same discipline as `profiles/prompt.py` and the same
division of labour: the model reports what the two documents say, and `score.py` does
the arithmetic. Three rules, each closing a specific failure:

  1. **Only requirements the posting states.** A model asked to judge fit will supply
     requirements from its own idea of the role — "5+ years of Kubernetes" about a
     posting that never mentions Kubernetes. That is §3.3's fabrication problem arriving
     a milestone early, on text the user reads and acts on. The live gate asserts every
     span is findable in the description; this is what makes that assertion passable.
  2. **Quote verbatim.** Same reason as M3's vault: a paraphrase is a *correct* claim
     that fails verification and gets dropped, which loses real signal rather than
     catching a fake one.
  3. **Do not score.** The score is a ratio over the partition below. A model that emits
     a number anchors on 85/90/75 and moves its whole distribution when the model version
     changes, which would invalidate the threshold the golden set exists to set.
"""

from db.models import Job
from schemas.prefs import Prefs
from schemas.profile import ProfileRead

SYSTEM = """\
You compare one job posting against one candidate profile. You are an assessor, not a \
recruiter and not an assistant.

Rules, in order of importance:

1. ONLY WHAT THE POSTING STATES. Every requirement you list must appear in the job \
description. Do not add requirements that are typical for the role, implied by the \
title, or standard in the industry. If the posting is vague, return few requirements — \
that is the honest answer and it is handled downstream.

2. QUOTE VERBATIM. Each entry in `met` and `missing` must be a span copied from the job \
description, not a rephrasing of one. Trim it to the requirement itself rather than the \
whole sentence, but do not reword it. A downstream check discards anything it cannot \
find in the posting.

3. DO NOT SCORE, RANK, OR RECOMMEND. Do not emit a number, a percentage, a verdict, or \
advice about applying. You partition the requirements; something else does the \
arithmetic.

4. SEPARATE FATAL FROM MERELY UNMET. A requirement belongs in `disqualifiers` only when \
BOTH hold: the posting states it as mandatory, AND the profile fails it. Everything else \
that is unmet goes in `missing`. When you are unsure, use `missing` — it is the safe \
answer, because a requirement wrongly called fatal removes a job the candidate could \
have had.

How to decide:

- `met`: the profile shows direct evidence of this requirement — a skill it lists, a \
responsibility in a role it held, a credential it names.
- `missing`: the posting states it and the profile does not evidence it. Absence of \
evidence goes here. Do not credit a requirement because the candidate could plausibly \
learn it or because an adjacent skill is close enough.
- `disqualifiers`: the narrow set the candidate cannot satisfy by being hired, and which \
no amount of other strength offsets. In practice: the legal right to work where the role \
is based; a citizenship, residency or security-clearance requirement; a working language \
the profile shows no evidence of; a location or timezone the posting explicitly refuses \
to consider candidates outside of; a licence or certification the role cannot legally be \
performed without.

  It is NOT a disqualifier when the posting says preferred, desired, a plus, nice to \
have, a bonus, or ideally. It is NOT a disqualifier because the candidate has fewer \
years than asked, is at a different seniority, lacks a named tool, or went to no \
university. Those are `missing`.

  Read the whole posting for these — they are usually one sentence near the end, after \
the responsibilities, and often in the legal boilerplate rather than the requirements \
list.
- A requirement stated twice counts once.
- `summary`: one sentence, addressed to the candidate, naming the strongest match and \
the most important gap. No score, no encouragement, no advice.

Return only the JSON object described by the schema.\
"""

# Both documents fenced, for the reason M3's are: a job description that contains
# something reading like an instruction must be visibly data rather than direction.
USER_TEMPLATE = """\
Compare the posting and the profile below.

<posting>
Title: {title}
Company: {company}
Location: {location}
Remote: {remote}

{description}
</posting>

<profile>
Seniority: {seniority}
Locations: {locations}
Work authorisation: {work_auth}
Authorised to work in: {work_auth_regions}
Stated on the résumé: {work_auth_stated}
Target roles: {titles}

{resume}
</profile>\
"""

# Descriptions run long and the tail is boilerplate — benefits, EEO statements, the
# company's mission. The requirements are near the top, and paying to send 20,000
# characters of legal text on every one of forty jobs is the cost this rung exists to
# control. ponytail: a fixed cut; move to a requirements-section heuristic if the gate
# shows requirements being truncated away.
MAX_DESCRIPTION_CHARS = 8_000
MAX_RESUME_CHARS = 12_000


def build(job: Job, profile: ProfileRead, prefs: Prefs, resume: str) -> str:
    return USER_TEMPLATE.format(
        title=job.title,
        company=job.company,
        # The exact city, which the hard filter deliberately no longer decides on — it
        # is coarse precisely so this stage can weigh the specifics.
        location=", ".join(job.locations) or job.location or "not stated",
        remote=job.remote_mode or "not stated",
        description=(job.description or "")[:MAX_DESCRIPTION_CHARS],
        seniority=profile.seniority or "not stated",
        locations=", ".join(profile.locations) or "not stated",
        work_auth=profile.work_auth or "not stated",
        # `work_auth` alone is country-less — "citizen" of where? M4's first gate scored a
        # UK citizen 94 on an ITAR-restricted role because nothing in this block could
        # contradict the bare word. Two forms, because they fail differently: the derived
        # region list is precise but only as good as `derive._REGION_WORDS`, and the
        # résumé's own sentence carries whatever that missed.
        work_auth_regions=", ".join(profile.work_auth_regions or []) or "not stated",
        work_auth_stated=profile.parsed_json.get("work_authorization") or "not stated",
        titles=", ".join(prefs.titles) or "not stated",
        resume=resume[:MAX_RESUME_CHARS],
    )
