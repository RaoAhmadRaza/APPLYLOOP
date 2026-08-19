import type { PipelineRow } from "@/lib/types";

// `met`/`missing`/`disqualifiers` are verbatim spans quoted from the posting;
// `bars` are sentences this system wrote. Styled differently on purpose — the
// live gate asserts the distinction is real (DEMO_PLAN §6, screen 3).
export function Verdict({ row }: { row: PipelineRow }) {
  const r = row.reasons_json;
  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-6">
      <div className="flex items-baseline justify-between">
        <div>
          <span className="text-3xl font-bold text-neutral-900">{row.score ?? "—"}</span>
          <span className="ml-1 text-sm text-neutral-400">/ threshold {r.threshold}</span>
        </div>
        <span className="rounded-full bg-neutral-100 px-2.5 py-1 text-xs font-medium text-neutral-600">
          {row.status}
        </span>
      </div>
      <p className="mt-2 text-sm text-neutral-700">{r.summary}</p>

      <ReasonList label="Met" items={r.met} icon="✅" quoted />
      <ReasonList label="Missing" items={r.missing} icon="❌" quoted />
      <ReasonList label="Disqualifiers" items={r.disqualifiers} icon="🚫" quoted danger />
      <ReasonList label="Bars" items={r.bars} icon="⛔" quoted={false} danger />

      <div className="mt-4 grid grid-cols-3 gap-3 border-t border-neutral-100 pt-3 text-xs text-neutral-500">
        <div>coverage {r.coverage != null ? `${Math.round(r.coverage * 100)}%` : "—"}</div>
        <div>similarity {r.similarity.toFixed(2)}</div>
        <div className="truncate" title={`${r.model} / ${r.embed_model}`}>
          {r.model}
        </div>
      </div>
    </section>
  );
}

function ReasonList({
  label,
  items,
  icon,
  quoted,
  danger,
}: {
  label: string;
  items: string[];
  icon: string;
  quoted: boolean;
  danger?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <div className="mt-3">
      <div className="text-xs font-medium uppercase text-neutral-400">
        {icon} {label}
      </div>
      <ul className="mt-1 space-y-1">
        {items.map((item, i) => (
          <li
            key={i}
            className={`text-sm ${danger ? "text-red-700" : "text-neutral-700"} ${
              quoted ? "italic" : ""
            }`}
          >
            {quoted ? `“${item}”` : item}
          </li>
        ))}
      </ul>
    </div>
  );
}
