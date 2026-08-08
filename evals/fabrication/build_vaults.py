"""Write `vaults.json`: the real evidence vault for each committed résumé fixture.

    uv run python evals/fabrication/build_vaults.py

BAR.md §7 requires cases to be judged against a vault M3's own parser produced, not one
written by hand — a hand-written vault means the validator is fed by its author. The
parsed résumés already exist in `evals/golden/pairs.json`, put there by M4's worksheet
builder running the real parse over the fixtures, so this makes **no model call**: it
replays `vault.claims` and `vault.is_supported` over stored `parsed_json` and the
`master_resume` each claim must be found in.

Derived data, separated from `cases.json` on purpose. Vaults are regenerable and a
fixture change should regenerate them; cases are human-authored judgement and must never
be regenerated. That is the seam.

The short handles (`E7`) are this file's output too, and they are stable because the
claim order is: they come from `vault.claims`, which walks the résumé in document order.
A case that cites E7 keeps meaning the same claim unless the résumé itself changes — at
which point the case *should* break rather than silently point somewhere else.
"""

import json
from pathlib import Path

from schemas.resume import ParsedResume

from workers.profiles import vault

HERE = Path(__file__).resolve().parent
PAIRS = HERE.parent / "golden" / "pairs.json"
OUT = HERE / "vaults.json"


def main() -> None:
    profiles = json.loads(PAIRS.read_text())["profiles"]

    vaults = {}
    for name in sorted(profiles):
        blob = profiles[name]
        resume = ParsedResume.model_validate(blob["parsed_json"])
        source = blob["master_resume"]

        claims = [claim for claim in vault.claims(resume) if vault.is_supported(claim.text, source)]
        vaults[name] = {
            # Every string the résumé's *structure* supplies. A bullet may name its own
            # employer or its own title without that being a new claim, so the validator
            # needs them and they are not evidence rows.
            "companies": [entry.name for entry in resume.work],
            "titles": [entry.position for entry in resume.work],
            "claims": [
                {
                    "id": f"E{index + 1}",
                    "kind": claim.kind.value,
                    "text": claim.text,
                    "source": claim.source,
                }
                for index, claim in enumerate(claims)
            ],
        }

    OUT.write_text(
        json.dumps(
            {
                "_meta": {
                    "bar": "evals/fabrication/BAR.md",
                    "built_from": "evals/golden/pairs.json",
                    "built_by": "evals/fabrication/build_vaults.py",
                    "note": "Derived. Regenerate when a résumé fixture changes; never hand-edit.",
                },
                "vaults": vaults,
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n"
    )
    for name, blob in vaults.items():
        print(f"{name:24} {len(blob['claims']):3} claims")


if __name__ == "__main__":
    main()
