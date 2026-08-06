"""The `companies` side of M1 — the slug registry §4.3 calls the moat."""

from db.models import Company
from schemas.enums import AtsType, CompanyStatus
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from workers.scraping import registry


def test_seeding_an_empty_registry_inserts_every_board(session: Session) -> None:
    inserted = registry.seed_registry(session)

    assert inserted == len(registry.SEED)
    assert session.scalar(select(func.count()).select_from(Company)) == len(registry.SEED)


def test_seeding_twice_is_a_no_op(session: Session) -> None:
    """`make seed` is run by hand and by the smoke test; it must not duplicate boards."""
    registry.seed_registry(session)

    assert registry.seed_registry(session) == 0
    assert session.scalar(select(func.count()).select_from(Company)) == len(registry.SEED)


def test_every_seeded_board_has_an_adapter(session: Session) -> None:
    """A seed row whose ats_type has no adapter would raise on its first ingest run."""
    from workers.scraping import ADAPTERS

    assert {ats for ats, _, _ in registry.SEED} <= set(ADAPTERS)


def test_seeded_boards_start_active_so_the_scheduled_run_finds_them(session: Session) -> None:
    registry.seed_registry(session)

    statuses = set(session.scalars(select(Company.status)))
    assert statuses == {CompanyStatus.ACTIVE.value}


def test_register_creates_a_board_with_its_domain(session: Session) -> None:
    company = registry.register(session, AtsType.GREENHOUSE, "vanta", "Vanta", "www.vanta.com")

    assert company.ats_type == AtsType.GREENHOUSE.value
    assert company.ats_slug == "vanta"
    assert company.domain == "www.vanta.com"


def test_registering_the_same_board_twice_updates_rather_than_duplicates(
    session: Session,
) -> None:
    """Two detect runs racing on one company must not split the change-detection state."""
    first = registry.register(session, AtsType.LEVER, "acme", "Acme", "acme.com")

    second = registry.register(session, AtsType.LEVER, "acme", "Acme Corporation", "acme.com")

    assert first.id == second.id
    assert second.name == "Acme Corporation"
    assert session.scalar(select(func.count()).select_from(Company)) == 1


def test_a_name_only_detection_never_erases_a_known_domain(session: Session) -> None:
    """detect() by company name has no URL to derive a domain from. Writing NULL there
    would lose information M2's reverse-index depends on."""
    registry.register(session, AtsType.ASHBY, "acme", "Acme", "acme.com")

    company = registry.register(session, AtsType.ASHBY, "acme", "Acme", None)

    assert company.domain == "acme.com"


def test_domain_of_only_parses_urls() -> None:
    assert registry.domain_of("https://www.vanta.com/careers") == "www.vanta.com"
    assert registry.domain_of("Acme Corp") is None
