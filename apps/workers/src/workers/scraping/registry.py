"""Everything that writes to `companies` — the slug registry §4.3 calls the moat.

§4.3 splits the registry into seed / grow / maintain. `SEED` below is only *seed* —
enough real boards that a scheduled run has something to do on a fresh database. *Grow*
is the reverse-index off aggregator rows, which needs Layer 2 and lands in M2.

Every slug in SEED was fetched from its live endpoint and returned a non-empty board on
2026-08-06. It is operational data rather than code — expect the M2 reverse-index to
replace this list, not to extend it by hand forever.

This lives here rather than in `workers/tasks/` so the integration suite can seed the
tables, run it, and assert the delta without a broker in the way (Part 10).
"""

from typing import Any
from urllib.parse import urlparse

from db.models import Company
from schemas.enums import AtsType
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

SEED: list[tuple[AtsType, str, str]] = [
    (AtsType.GREENHOUSE, "stripe", "Stripe"),
    (AtsType.GREENHOUSE, "vercel", "Vercel"),
    (AtsType.GREENHOUSE, "databricks", "Databricks"),
    (AtsType.GREENHOUSE, "anthropic", "Anthropic"),
    (AtsType.GREENHOUSE, "figma", "Figma"),
    (AtsType.GREENHOUSE, "robinhood", "Robinhood"),
    (AtsType.GREENHOUSE, "coinbase", "Coinbase"),
    (AtsType.GREENHOUSE, "airtable", "Airtable"),
    (AtsType.GREENHOUSE, "discord", "Discord"),
    (AtsType.GREENHOUSE, "reddit", "Reddit"),
    (AtsType.GREENHOUSE, "gitlab", "GitLab"),
    (AtsType.GREENHOUSE, "cloudflare", "Cloudflare"),
    (AtsType.LEVER, "spotify", "Spotify"),
    (AtsType.LEVER, "shieldai", "Shield AI"),
    (AtsType.LEVER, "gopuff", "Gopuff"),
    (AtsType.LEVER, "binance", "Binance"),
    (AtsType.LEVER, "tala", "Tala"),
    # Ashby board names are case-sensitive — lowercasing one 404s the endpoint.
    (AtsType.ASHBY, "linear", "Linear"),
    (AtsType.ASHBY, "openai", "OpenAI"),
    (AtsType.ASHBY, "ramp", "Ramp"),
    (AtsType.ASHBY, "posthog", "PostHog"),
    (AtsType.ASHBY, "supabase", "Supabase"),
    (AtsType.ASHBY, "modal", "Modal"),
    (AtsType.ASHBY, "hex", "Hex"),
    (AtsType.WORKABLE, "blueground", "Blueground"),
    (AtsType.WORKABLE, "persado", "Persado"),
    (AtsType.WORKABLE, "skroutz", "Skroutz"),
    (AtsType.SMARTRECRUITERS, "Visa", "Visa"),
    (AtsType.RECRUITEE, "channable", "Channable"),
    (AtsType.RECRUITEE, "vandebron", "Vandebron"),
]


def seed_registry(session: Session) -> int:
    """Insert any missing seed board. Returns how many rows were new.

    ON CONFLICT DO NOTHING rather than check-then-act, for the same reason Part 13
    rule 10 gives: the unique constraint is the arbiter, so running this twice — or
    twice concurrently — is a no-op the second time.
    """
    rows: list[dict[str, Any]] = [
        {"ats_type": ats.value, "ats_slug": slug, "name": name} for ats, slug, name in SEED
    ]
    statement = (
        insert(Company)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[Company.ats_type, Company.ats_slug])
        .returning(Company.id)
    )
    inserted = session.scalars(statement).all()
    session.flush()
    return len(inserted)


def register(session: Session, ats: AtsType, slug: str, name: str, domain: str | None) -> Company:
    """Insert a detected board, or refresh what we know if we already had it.

    The unique constraint decides, not a prior SELECT — same reasoning as Part 13
    rule 10. Two detect runs racing on the same company must not produce two rows.
    """
    statement = insert(Company).values(ats_type=ats.value, ats_slug=slug, name=name, domain=domain)
    # Only overwrite the domain when this call actually resolved one: detect() by
    # company name has no URL to derive it from, and a null must not erase a known value.
    updates: dict[str, Any] = {"name": statement.excluded.name}
    if domain:
        updates["domain"] = statement.excluded.domain

    company_id = session.scalars(
        statement.on_conflict_do_update(
            index_elements=[Company.ats_type, Company.ats_slug], set_=updates
        ).returning(Company.id)
    ).one()
    session.flush()
    # populate_existing: the Core statement wrote around the ORM, so a plain get() would
    # hand back whatever the identity map cached before the update.
    return session.get_one(Company, company_id, populate_existing=True)


def domain_of(url_or_name: str) -> str | None:
    """The hostname, when detect() was given a URL rather than a company name."""
    if not url_or_name.startswith(("http://", "https://")):
        return None
    return urlparse(url_or_name).hostname
