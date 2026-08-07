# Residential proxy setup (layer 2 only)

> Operational guide for the one paid dependency in the sourcing stack. Everything here
> is measured against this repo's actual configuration, not estimated.
>
> **Scope:** the aggregator layer (`workers/scraping/aggregator.py`) and nothing else.
> CLAUDE.md Part 13 rule 12 forbids proxying layers 1 and 3, and `http.client()` sets
> `trust_env=False` so an ambient `HTTPS_PROXY` cannot leak into them.

---

## 1. Why it is necessary — the honest version

Not for the reason people usually give.

**It is not because scraping fails without one.** Measured 2026-08-07 from this
development machine, unproxied:

| site | rows | requests | bytes |
|---|---|---|---|
| indeed | 50 | 1 | 1,068 KiB |
| linkedin | 50 | 5 | 142 KiB |
| google | **0** | 1 | 89 KiB |

Indeed and LinkedIn both returned a full result set. So the question is not "does it
work" but **"does it work from where production runs"**, and those are different places:

- The dev machine sits behind a **residential ISP**. LinkedIn and Indeed see a normal
  home IP.
- Production is a **Hetzner CX32** (§5.5) — a datacenter ASN. Datacenter ranges are the
  first thing an anti-bot vendor blocks, because no real job seeker browses from one.

That gap is the whole justification. The proxy does not make scraping *possible*; it
makes it possible **from a server**.

Three secondary reasons, in order of how much they actually matter:

1. **A burnt IP is your whole box.** Hetzner gives you one address. If LinkedIn flags it,
   every future request from that machine is flagged, including anything else you ever
   run there. A proxy IP is disposable; your server's is not.
2. **Rate limits are per IP.** LinkedIn throttles around the tenth page per IP (§5.4). A
   pool spreads that; one address does not.
3. **Blast radius.** §11 wants each risk failing inside one module. A proxy keeps the
   aggregator's reputation problem off the address the API, the ATS layer and the feeds
   all share.

**What it is emphatically not for:** layers 1 and 3. Direct ATS JSON and the eight free
feeds are public endpoints that want to be read, cost nothing, and have never rate-limited
us. Proxying them spends money to make first-party data slower.

### Google is already broken, proxy or not

`google` returned **0 rows unproxied from a residential IP**. That is not a blocking
problem a proxy solves — it is either a JobSpy selector rotting or the search shape being
wrong. Do not buy a proxy expecting it to fix Google. Either debug it or drop it from
`aggregator.SITES`; it is one list entry.

---

## 2. What it actually costs

The number that decides the bill is bandwidth, and nobody measures it. This repo's
configured pass is:

```
6 search terms x 3 sites = 18 searches
measured: 1 search across 3 sites = 1.27 MiB
=> one full pass = 7.6 MiB
```

At `AGGREGATE_INTERVAL_MINUTES=720` (the default, twice a day):

| cadence | per day | per month | @ $1.75/GB | @ $4/GB | @ $12/GB |
|---|---|---|---|---|---|
| **every 12h (default)** | 15 MiB | **0.45 GB** | **$0.78** | **$1.79** | $5.36 |
| every 6h | 30 MiB | 0.89 GB | $1.56 | $3.57 | $10.72 |
| hourly | 183 MiB | 5.4 GB | $9.38 | $21.43 | $64.29 |
| 10x the terms, hourly | 1.8 GiB | 54 GB | $94 | $214 | $643 |

**At the shipped configuration this is roughly one dollar a month.**

> ⚠️ **This contradicts CLAUDE.md §8.1**, which budgets "**$50 → hundreds**" and "2–5x
> infra cost" for proxies and calls them "the swing factor". That estimate is right for
> the workload in the last row of the table — dozens of search terms, hourly, with
> descriptions fetched per job. It is off by two orders of magnitude for what M2 actually
> runs. §8.1 has not been changed; the estimate becomes true again the moment the search
> list or the cadence grows, so treat the table above as "today", not "forever".

The two knobs that move this by 10x or more:

- **`aggregator.SEARCHES`** — linear. Six terms today.
- **`FETCH_DESCRIPTIONS`** — currently `False`. Turning it on costs one extra request
  *per job* and is what makes LinkedIn 142 KiB become tens of MiB. §3.5 says filter
  before you spend: M4 should fetch descriptions for the shortlist, not for every row.

---

## 3. Which provider

At under 1 GB/month the per-GB rate barely matters — **expiry does**. Most providers void
unused bandwidth at the end of the billing cycle, so a 50 GB plan would waste 49 GB every
month.

| provider | entry rate | traffic expires? | verdict |
|---|---|---|---|
| **IPRoyal** | ~$7/GB at 1 GB, sliding to $1.75/GB at 1 TB | **No — balance is non-expiring** | **Start here.** No contract, no minimum, no sales call. At our volume one small top-up lasts many months. |
| Decodo (ex-Smartproxy) | ~$2–6/GB | yes, monthly | Good mid-market. Worth it only once volume is steady. |
| Webshare | from ~$3.50/mo | yes | Cheapest at *low* bandwidth; check the current residential rate. |
| Bright Data / Oxylabs | $4–12/GB | yes | Enterprise tooling and compliance paperwork we do not need yet. |

**Recommendation: IPRoyal pay-as-you-go, smallest top-up.** Non-expiring traffic is the
feature that fits a sub-1-GB workload; everyone else's model assumes you burn the plan.

Re-evaluate when monthly usage passes ~20 GB, where the per-GB tiers start to dominate.

---

## 4. Setting it up

**1. Buy traffic.** IPRoyal → Royal Residential → pay as you go, smallest amount. You get
credentials shaped like:

```
user-<username>-country-us:<password>@geo.iproyal.com:12321
```

**2. Put them in `.env`.** Comma-separated, one entry per exit:

```bash
JOBSPY_PROXIES=user-abc-country-us:pw@geo.iproyal.com:12321,user-abc-country-us:pw2@geo.iproyal.com:12321
```

Rules the code enforces, and why:

- **Never** set `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`. Those are ambient and would
  route the ATS layer and every free feed through metered bandwidth. `http.client()`
  passes `trust_env=False` and `tests/unit/test_proxy_scope.py` fails if anyone removes it.
- The value is read from settings **inside** the task, never passed as a Celery argument —
  Celery logs task arguments at INFO and writes them to the Redis result backend, so a
  proxy parameter would print `user:pass@host` into both.
- It is a `SecretStr`, so `repr(get_settings())` cannot leak it into a log or a Sentry
  breadcrumb.
- `.env` is gitignored. Never commit these (Part 13 rule 9).

**3. Restart the worker** so the cached settings are rebuilt:

```bash
docker compose up -d --build worker beat
```

---

## 5. Verifying it works

```bash
make verify-live-aggregator     # APPLYLOOP_LIVE_AGGREGATOR=1, skipped without JOBSPY_PROXIES
```

That suite asserts rows actually come back through the proxy for every site in
`aggregator.SITES`, that ids are unique within a search, and that the layer still no-ops
when the proxy list is empty. It is not in CI — it spends real bandwidth.

Then one real pass:

```bash
docker compose exec -T worker python -c \
  "from workers.tasks.scraping import aggregate_all; print(aggregate_all.delay().get(timeout=60))"
```

A non-zero return means it fanned out. Zero means the interlock fired — the proxy list is
empty, or the worker did not pick up the new `.env`.

Confirm rows landed and are attributable to the layer:

```sql
SELECT source, count(*) FROM jobs WHERE source LIKE 'jobspy:%' GROUP BY 1;
SELECT payload_json FROM events WHERE type IN ('aggregate.run','aggregate.skipped')
ORDER BY id DESC LIMIT 10;
```

---

## 6. What to watch

§3.7 and §8.2: **alert on volume, not just errors.** A blocked scraper returns an empty
DataFrame and raises nothing, so silence is the failure mode.

- `aggregate.run` events carry `fetched` per `(site, term, location)`. A site that drops
  to zero is blocked, not quiet.
- `aggregate.skipped` means the interlock fired. In production that is a misconfiguration.
- Watch bandwidth on the provider dashboard against the table in §2. A sudden 10x means
  something turned `FETCH_DESCRIPTIONS` on, or the search list grew.

---

## 7. When to skip the proxy entirely

Legitimate, and worth saying out loud:

- **Layer 1 and layer 3 already give you thousands of jobs for free.** The live run behind
  M2's gate produced 942 rows from three ATS boards and one feed, with the reverse-index
  discovering a fourth board on its own. Layer 2 is breadth, not the backbone (§7.4).
- If you are running the pipeline **from a residential connection** — a home server, a
  laptop — the measurement at the top of this document says you may not need one at all.
- The aggregator is interlocked: with `JOBSPY_PROXIES` empty it records
  `aggregate.skipped` and does nothing. That is a supported state, not a broken one. The
  rest of M2 runs fine without it.

The one thing not to do is run the aggregator unproxied **from the production server**.
That is the case this whole document exists for.
