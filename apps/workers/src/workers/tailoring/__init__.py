"""M5 — documents: tailor a résumé and a cover letter for one match, and prove them.

Reads `matches` at `status='discovered'`, plus the job, the profile and the evidence
vault. Writes `documents` rows and moves the match to `tailored`. Rows in, rows out
(§3.1): this package imports `db`, `schemas`, `storage`, `workers.llm` and
`workers.text`, and never `workers.profiles` or `workers.matching`. The vault it
validates against arrives as evidence rows, not as a function call into M3.

    select    the match, the profile, the vault, as plain structures
    prompt    two prompts — a résumé call and a letter call, never one that does both
    validate  THE GUARDRAIL. §3.3 enforcement point 2, the one that is not a prompt
    words     the tokens a rewrite may add, because they assert nothing
    render    validated content -> RenderCV YAML -> PDF, and a Typst letter
    tailor    the stage: one match in, two stored documents or one loud block

**The order in `tailor.py` is not arrangeable.** Validation happens before rendering, not
after — Part 13 rule 2 says no generated text reaches a PDF without passing the validator,
and a validator that runs on a rendered document is auditing rather than preventing.
"""
