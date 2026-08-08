"""§3.3 enforcement point 1. **Not the guardrail** — see validate.py for that one.

The prompt's job is to make the validator's job small. It cannot make it unnecessary:
prompts regress silently and a test does not, and this repo has already watched a matching
prompt regress differently on each of four iterations.

**Two calls, not one.** M4 measured a prompt's accuracy fall from 8/8 to 9/13 when one
rule was widened to cover two more categories, and a résumé call and a letter call want
different things — selection and compression against argument and voice. The extra call
costs a few cents on the one output §7.2 says is worth spending on.

**The posting is what to select *for*, never a source of facts.** That sentence is in
both system prompts because M4 measured this exact collision from the other side: once
the résumé and the description shared a prompt, the model began quoting the candidate's
own CV sentences back as posting requirements. The pull runs both ways, and the
validator's allowed universe is the vault alone.
"""

from workers.tailoring.validate import Claim

# The description is the longest thing in the prompt and the least information-dense per
# token. Same cap M4 uses, for the same reason.
MAX_DESCRIPTION_CHARS = 8_000

RESUME_SYSTEM = """You tailor a résumé for one job, working only from evidence supplied.

You are given a numbered EVIDENCE list taken verbatim from the candidate's own résumé,
and a job posting. Select the evidence that best fits the posting and rewrite it for
clarity. That is the whole task.

Absolute rules:
1. Every bullet you return must cite the ONE evidence id it is a rewrite of.
2. A bullet may only contain facts already in the evidence it cites. No technology, no
   employer, no metric, no scope, no seniority that is not in that specific item.
3. Numbers must be copied exactly from the cited evidence. Never convert, round,
   recompute, or move a number from one item to another.
4. Never take a word from the job posting unless the cited evidence already contains it.
   The posting tells you WHICH evidence matters. It is not a source of facts about this
   candidate, and a requirement it states is not something the candidate has done.
5. Skills must be copied exactly from evidence items of kind `skill`. Do not expand an
   abbreviation, do not add a related tool, do not translate one vendor's name to
   another's.
6. If the posting asks for something the evidence does not show, say nothing about it.
   Leaving a gap is correct. Filling it is the one thing that must never happen.

Prefer fewer, stronger bullets over covering everything. Anything you cannot ground will
be removed before rendering, so an ungrounded bullet is a bullet you wasted."""

LETTER_SYSTEM = """You write the body of a cover letter, working only from evidence supplied.

You are given a numbered EVIDENCE list taken verbatim from the candidate's own résumé,
the employer's name, the role, and the posting. Write three short paragraphs of connected
prose — an argument, not a list of achievements restated.

Absolute rules:
1. Every paragraph cites the evidence ids it draws on.
2. A paragraph may only contain facts from the evidence it cites, plus the employer's
   name and the role title as given. Nothing else.
3. Numbers are copied exactly from the cited evidence.
4. Never take a claim from the job posting. It states what the employer wants, never
   what this candidate has done.
5. No years-of-experience totals, no "passionate about", no credential, no team size,
   and no adjective about the candidate that the evidence does not support.
6. Name the company and the role at most once, in the first paragraph. Repeating them
   is the failure mode of writing under these constraints, not a way of satisfying them.

Write the greeting and sign-off nowhere — they are added afterwards. Start at the first
body paragraph. A paragraph that cannot be grounded fails the whole letter, so write
three you can stand behind rather than four you cannot."""

_EVIDENCE = "{handle}  [{kind}]  {text}"

RESUME_USER = """EVIDENCE (the entire universe of permissible claims):
{evidence}

JOB POSTING
Title: {title}
Company: {company}
{description}

Return the bullets you would put on this résumé, each citing its evidence id, and the
skills to list, copied exactly."""

LETTER_USER = """EVIDENCE (the entire universe of permissible claims):
{evidence}

THE ROLE
Company: {company}
Title: {title}
{description}

Return three body paragraphs, each citing the evidence ids it draws on."""


def evidence_block(claims: list[Claim]) -> str:
    """The list the model may draw on, and nothing else.

    Kind is shown because the rules differ by kind — a `skill` is copied exactly, a
    `bullet` may be rewritten — and because a model that cannot see the difference will
    cite a skill for a piece of work, which the validator then refuses.
    """
    return "\n".join(
        _EVIDENCE.format(handle=claim.id, kind=claim.kind, text=claim.text.replace("\n", " "))
        for claim in claims
    )


def build_resume(*, claims: list[Claim], title: str, company: str, description: str | None) -> str:
    return RESUME_USER.format(
        evidence=evidence_block(claims),
        title=title,
        company=company,
        description=(description or "")[:MAX_DESCRIPTION_CHARS],
    )


def build_letter(*, claims: list[Claim], title: str, company: str, description: str | None) -> str:
    return LETTER_USER.format(
        evidence=evidence_block(claims),
        title=title,
        company=company,
        description=(description or "")[:MAX_DESCRIPTION_CHARS],
    )


def relevant(claims: list[Claim]) -> list[Claim]:
    """Which claims the résumé call may draw on.

    `title` and `credential` claims are dropped: the renderer takes titles and education
    from `parsed_json` directly, so sending them only invites a bullet that cites one —
    which the validator refuses anyway, having cost tokens in both directions.
    """
    return [claim for claim in claims if claim.kind in {"bullet", "skill"}]


def for_letter(claims: list[Claim]) -> list[Claim]:
    """Which claims the *letter* call may draw on. Bullets only.

    Measured, not reasoned: M5's first real cover letter contained the sentence
    "Languages: Python, Go, SQL, TypeScript. Infrastructure: Kubernetes, Terraform,
    PostgreSQL, Redis, Kafka." — grounded, traceable, and not a sentence. The model had
    skill claims available and the validator requires it to quote what it cites, so a
    cited skill *becomes* a list in the middle of a paragraph.

    A skills list belongs on the résumé, which is one document over. Withholding them
    here removes the temptation instead of asking the prompt to resist it.
    """
    return [claim for claim in claims if claim.kind == "bullet"]
