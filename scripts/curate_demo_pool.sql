-- Demo-pool curation, 2026-08-17. Hand curation per DEMO_PLAN task 2, not a deduper fix
-- (the fuzzy-dedupe trigger stays deferred — CLAUDE.md §6.3, PROJECT_STATE "Known broken").
--
-- Collapses same-(company, title) duplicates within Bluelight Consulting and Jobgether
-- down to one survivor each, for jobs currently in the deduped pool
-- (closed_at IS NULL AND canonical_id IS NULL).
--
-- Marks losers via canonical_id rather than deleting them. §6.3 is explicit that a
-- delete gets silently reinserted by the next scheduled ingest — upsert conflicts on
-- (source, external_id), so a deleted row has no conflict target and reappears within
-- one beat tick (every 6h). canonical_id is the same mechanism the real cross-source
-- dedupe already uses, applied once by hand.
--
-- Survivor = latest posted_at (the freshest listing), tiebroken by id. Everything else
-- in the group points canonical_id at it and drops out of every reader that filters on
-- "canonical_id IS NULL" (the pipeline endpoint, §6.3).

begin;

with ranked as (
    select
        id,
        row_number() over (
            partition by company, title
            order by posted_at desc nulls last, id
        ) as rn,
        first_value(id) over (
            partition by company, title
            order by posted_at desc nulls last, id
        ) as survivor_id
    from jobs
    where closed_at is null
      and canonical_id is null
      and company in ('Bluelight Consulting', 'Jobgether')
)
update jobs
set canonical_id = ranked.survivor_id
from ranked
where jobs.id = ranked.id
  and ranked.rn > 1;

-- Sanity check: no top-line duplicate title should remain in the live pool for these
-- two companies. Expect zero rows back.
select company, title, count(*)
from jobs
where closed_at is null and canonical_id is null
  and company in ('Bluelight Consulting', 'Jobgether')
group by company, title
having count(*) > 1;

commit;
