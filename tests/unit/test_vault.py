"""The M3 half of the zero-fabrication guarantee (§3.3).

M5 owns `test_fabrication_guard`, which is permanent and adversarial and lives in
`evals/`. This file is its upstream sibling and answers a different question: not "does
the validator catch an invented bullet?" but "**is the thing the validator diffs against
actually true?**"

If the parser invents a skill, M5's validator finds it, declares the generated bullet
traceable, and puts a lie on a résumé — working perfectly and proving nothing. So a
claim is stored only if its text is present in the source, and that is asserted here.
"""

from schemas.resume import (
    ParsedResume,
    ResumeCertificate,
    ResumeEducation,
    ResumeProject,
    ResumeSkill,
    ResumeWork,
)
from workers.profiles import vault

RESUME = """\
# Ada Lovelace
Senior Backend Engineer — Berlin, DE

## Experience
### Staff Engineer, Acme GmbH (2021-03 — present)
- Cut p95 checkout latency from 900ms to 210ms by adding a read-through cache
- Led the migration of 40 services from Nomad to Kubernetes

### Backend Engineer, Beta Ltd (2018-01 — 2021-02)
- Built the billing reconciliation pipeline processing 2M events a day

## Projects
### pgqueue
- Wrote an open-source Postgres job queue used by 300 repositories

## Education
Technische Universität Berlin — BSc Computer Science

## Certifications
Certified Kubernetes Administrator

## Skills
Languages: Python, Go, SQL
Infrastructure: Kubernetes, Terraform
"""


# ------------------------------------------------------------------- verification


def test_a_claim_present_in_the_resume_is_supported() -> None:
    assert vault.is_supported("Python", RESUME) is True
    assert vault.is_supported("Led the migration of 40 services from Nomad to Kubernetes", RESUME)


def test_a_claim_absent_from_the_resume_is_not_supported() -> None:
    """THE assertion. Everything §3.3 promises rests on this returning False."""
    assert vault.is_supported("Rust", RESUME) is False
    assert vault.is_supported("Scaled the platform to 10 million users", RESUME) is False


def test_a_paraphrase_is_not_supported() -> None:
    """A rewrite that means the same thing is still a claim the résumé does not make.
    This is why the comparison is containment and not similarity — a fuzzy threshold
    would wave exactly this through, and 'reduced latency by 76%' is a number nobody
    wrote down."""
    assert vault.is_supported("Reduced checkout latency by 76%", RESUME) is False


def test_line_breaks_and_spacing_do_not_reject_a_true_claim() -> None:
    """markitdown wraps. A claim that failed because a newline landed mid-sentence would
    silently drop real evidence, which is the worse of the two failure directions."""
    wrapped = "Cut p95 checkout latency\nfrom 900ms   to 210ms\tby adding a read-through cache"

    assert vault.is_supported(wrapped, RESUME) is True


def test_case_and_accents_do_not_reject_a_true_claim() -> None:
    assert vault.is_supported("technische universitat berlin", RESUME) is True


def test_an_empty_claim_is_never_supported() -> None:
    """The empty string is contained in everything. Without the guard, a blank claim
    would be "verified" against any résumé at all."""
    assert vault.is_supported("", RESUME) is False
    assert vault.is_supported("   ", RESUME) is False


# ----------------------------------------------------------------- claim extraction


def _parsed() -> ParsedResume:
    return ParsedResume(
        work=[
            ResumeWork(
                position="Staff Engineer",
                highlights=[
                    "Cut p95 checkout latency from 900ms to 210ms by adding a read-through cache",
                    "Led the migration of 40 services from Nomad to Kubernetes",
                ],
            )
        ],
        projects=[
            ResumeProject(
                name="pgqueue",
                highlights=["Wrote an open-source Postgres job queue used by 300 repositories"],
            )
        ],
        education=[ResumeEducation(institution="Technische Universität Berlin")],
        certificates=[ResumeCertificate(name="Certified Kubernetes Administrator")],
        skills=[
            ResumeSkill(name="Languages", keywords=["Python", "Go", "SQL"]),
            ResumeSkill(name="Infrastructure", keywords=["Kubernetes", "Terraform"]),
        ],
    )


def test_every_skill_keyword_becomes_its_own_claim() -> None:
    """§3.3 names inventing a *skill* as the adversarial case, so each one has to be
    individually checkable. A résumé writes "Languages: Python, Go" — leaving those
    buried inside a group label would make "is Rust in the vault?" unanswerable."""
    skills = {claim.text for claim in vault.claims(_parsed()) if claim.kind == "skill"}

    assert {"Python", "Go", "SQL", "Kubernetes", "Terraform"} <= skills


def test_a_group_line_left_whole_by_the_parser_is_split_into_its_members() -> None:
    """The model is asked for `{name: "Languages", keywords: [...]}` and sometimes returns
    the whole line in `name` instead. Which happens is a property of the run: the same
    fixture parsed twice gave both shapes, and the second one buried four real skills in
    one claim nothing could match. M5's first live run was blocked by exactly this."""
    parsed = ParsedResume(
        skills=[ResumeSkill(name="Languages: Python, Go, SQL, TypeScript", keywords=[])]
    )

    skills = {claim.text for claim in vault.claims(parsed) if claim.kind == "skill"}

    assert {"Python", "Go", "SQL", "TypeScript"} <= skills
    # The line itself stays a claim: it is what the résumé says, and a bullet quoting it
    # verbatim must still be traceable.
    assert "Languages: Python, Go, SQL, TypeScript" in skills
    # Its heading does not. A heading is not a skill, and one stored alone is selectable
    # onto a résumé and countable as an ATS keyword while being neither.
    assert "Languages" not in skills


def test_a_group_heading_with_keywords_is_not_itself_a_claim() -> None:
    """The shape the prompt asks for. `Languages` introduces the members and is not one
    of them — storing it put `Languages` and `Infrastructure` in the live vault as skills
    in their own right, and the ATS keyword count duly reported them missing."""
    parsed = ParsedResume(skills=[ResumeSkill(name="Languages", keywords=["Python", "Go"])])

    skills = {claim.text for claim in vault.claims(parsed) if claim.kind == "skill"}

    assert skills == {"Python", "Go"}


def test_a_lone_skill_with_no_members_is_kept() -> None:
    """`name` is only a heading when it introduces something. On its own it is the skill."""
    parsed = ParsedResume(skills=[ResumeSkill(name="Python", keywords=[])])

    skills = {claim.text for claim in vault.claims(parsed) if claim.kind == "skill"}

    assert skills == {"Python"}


def test_splitting_a_group_line_never_admits_a_prefix_of_a_member() -> None:
    """Splitting, not substring containment. `Postgres` is a prefix of `PostgreSQL` and
    `Java` of `JavaScript`; a containment check calls both members and the skills rule
    stops meaning anything. Case S-06 in the fabrication set is the paid half of this."""
    parsed = ParsedResume(
        skills=[ResumeSkill(name="Infrastructure: PostgreSQL, JavaScript", keywords=[])]
    )

    skills = {claim.text for claim in vault.claims(parsed) if claim.kind == "skill"}

    assert "PostgreSQL" in skills
    assert "Postgres" not in skills
    assert "Java" not in skills


def test_bullets_from_roles_and_projects_are_both_claims() -> None:
    """M5 tailors from both. A project bullet that was not in the vault would be
    stripped from the generated résumé as unbacked."""
    bullets = {claim.text for claim in vault.claims(_parsed()) if claim.kind == "bullet"}

    assert "Led the migration of 40 services from Nomad to Kubernetes" in bullets
    assert "Wrote an open-source Postgres job queue used by 300 repositories" in bullets


def test_titles_and_credentials_are_claims() -> None:
    """A fabricated job title or degree is a lie of the same class as a fabricated
    skill, and M5 restates both verbatim."""
    by_kind = {(claim.kind, claim.text) for claim in vault.claims(_parsed())}

    assert ("title", "Staff Engineer") in by_kind
    assert ("credential", "Technische Universität Berlin") in by_kind
    assert ("credential", "Certified Kubernetes Administrator") in by_kind


def test_every_claim_carries_a_source_pointer() -> None:
    """§3.3's "traceable to a source", singular, per claim. This is the string M5 cites."""
    for claim in vault.claims(_parsed()):
        assert claim.source


def test_a_one_character_claim_is_dropped() -> None:
    """A single letter is contained in almost any résumé, so storing it would create a
    claim that "verifies" against everything and means nothing."""
    resume = ParsedResume(skills=[ResumeSkill(name="C", keywords=["x"])])

    assert vault.claims(resume) == []


def test_every_extracted_claim_of_a_faithful_parse_survives_verification() -> None:
    """The end-to-end shape of the happy path: a parse that copied rather than
    summarised loses nothing. If this fails, the normaliser is too strict and the stage
    is quietly discarding real evidence."""
    unsupported = [
        claim.text
        for claim in vault.claims(_parsed())
        if not vault.is_supported(claim.text, RESUME)
    ]

    assert unsupported == []
