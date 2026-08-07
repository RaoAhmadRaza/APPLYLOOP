"""M3: a résumé in, a structured profile and an evidence vault out.

The stage reads `profiles` and writes `profiles.parsed_json`, four promoted columns,
and `evidence` rows. Nothing downstream imports from here — M4 and M5 read those rows
out of Postgres, which is what §3.1 means by the database being the only interface.

Five modules, split by what would otherwise be untestable together:

    extract.py   bytes -> markdown, via markitdown. No model, no database.
    prompt.py    the extraction instructions. Prose, versioned like code.
    derive.py    pure functions over a ParsedResume: years of experience, seniority
                 band, the promotion rule. **No LLM, deliberately** — see below.
    vault.py     claims out of a ParsedResume, each verified against the source text.
    parse.py     the stage function that sequences the four and writes the rows.

**The LLM extracts facts; Python derives filters.** Seniority and years-of-experience
are what M4 puts in a `WHERE` clause, and a value that varies between runs makes a
filter that silently drops different jobs each time. So the model is asked only for
what is written on the page — titles, dates, bullets — and the numbers M4 depends on
are computed from that, by code with tests.

**The vault is verified, not trusted.** §3.3 makes M5's validator the guardrail against
fabrication, but a validator that diffs against an unverified vault proves nothing: a
parser that invented a skill would have M5 faithfully declare a fabricated bullet
"traceable". Every claim's text must appear in `master_resume` or it is dropped, and
that check happens here, at write time.
"""
