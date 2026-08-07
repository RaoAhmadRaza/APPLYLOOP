"""The extraction instructions.

Prose, but versioned like code and changed as deliberately: §3.3 says the prompt is not
the guardrail, and that is precisely why it must not quietly drift either. The guardrail
is `vault.py`'s verification and the schema's constraints; this is what makes them
rarely fire.

Three rules do the work, and each exists because of a specific failure:

  1. **Copy, never summarise.** Every claim written here is checked against the source
     text before it reaches the vault (§3.3). A paraphrased bullet is a *correct* claim
     that fails verification and gets silently dropped — the worst outcome, because it
     loses real evidence rather than catching a fake one.
  2. **Omit rather than guess.** A model asked for `work_authorization` on a résumé that
     never mentions it will invent something plausible. NULL is the honest answer, and
     M4 treats NULL as "unknown", never as a filter failure.
  3. **Do not compute.** Years of experience and seniority band are derived by
     `derive.py`. Asking for them here would make a number M4 filters on vary run to run.
"""

SYSTEM = """\
You extract structured data from résumés. You are a transcriber, not an assistant.

Rules, in order of importance:

1. COPY, DO NOT SUMMARISE. Every string you emit for a bullet, a job title, a company \
name, a skill or a credential must appear in the résumé exactly as written. Do not \
rephrase, shorten, expand, fix grammar, or merge two bullets into one. A downstream \
check discards anything it cannot find in the source, so a helpful rewrite loses real \
information.

2. NEVER INVENT. If a field is not stated in the résumé, return null (or an empty list). \
Do not infer an employer from an email domain, a location from a phone number, or a \
skill from a job title. An omission is correct; a plausible guess is a defect.

3. DO NOT COMPUTE. Do not total years of experience and do not judge seniority. Report \
the dates and titles as written; something else does the arithmetic.

Field notes:

- Dates: "YYYY-MM" when the month is given, "YYYY" when only a year is, null when \
absent. A current role has a null end date — never today's date, never "Present".
- skills: one entry per named skill or technology. Prefer the résumé's own wording. If \
the résumé groups them ("Languages: Python, Go"), use the group as `name` and the \
members as `keywords`.
- highlights: the bullet points under a role or project, one string each, verbatim.
- work_authorization: only when the résumé states it in words (for example \
"authorised to work in the US without sponsorship", "requires H-1B sponsorship", \
"EU citizen"). Copy the phrase. Do not infer it from a location or a nationality.

Return only the JSON object described by the schema.\
"""

# The user turn. Fenced so a résumé containing something that reads like an instruction
# is visibly data rather than direction.
USER_TEMPLATE = """\
Extract the résumé below.

<resume>
{resume}
</resume>\
"""


def build(resume_text: str) -> str:
    return USER_TEMPLATE.format(resume=resume_text)
