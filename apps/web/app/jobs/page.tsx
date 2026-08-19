"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { MatchStatus, PipelineRow, PipelineSummary } from "@/lib/types";
import { useUser } from "@/components/UserContext";

const FILTERS: { label: string; value: MatchStatus | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Discovered", value: "discovered" },
  { label: "Tailored", value: "tailored" },
  { label: "Approved", value: "approved" },
  { label: "Applied", value: "applied" },
  { label: "Skipped", value: "skipped" },
];

const STATUS_STYLE: Record<string, string> = {
  discovered: "bg-neutral-200 text-neutral-700",
  tailored: "bg-blue-100 text-blue-700",
  queued: "bg-blue-100 text-blue-700",
  approved: "bg-green-100 text-green-700",
  applied: "bg-purple-100 text-purple-700",
  skipped: "bg-neutral-100 text-neutral-400",
};

export default function JobsPage() {
  const { userId } = useUser();
  const [filter, setFilter] = useState<MatchStatus | "all">("all");
  const [rows, setRows] = useState<PipelineRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Matches with a tailor request in flight — the worker is async and `discovered`
  // is the only status until it lands, so we poll rather than fake a transition.
  const [tailoring, setTailoring] = useState<Set<string>>(new Set());
  const [summary, setSummary] = useState<PipelineSummary | null>(null);
  const [profileId, setProfileId] = useState<string | null>(null);
  // Stays true once fired — the request itself resolves fast (it only enqueues), but
  // the matcher run behind it takes minutes, so "queued" should stick, not flash and
  // re-enable a button that would just queue a second run.
  const [triggered, setTriggered] = useState(false);

  const refresh = () => {
    if (!userId) return;
    api
      .getPipeline(userId, filter === "all" ? undefined : filter)
      .then((page) => {
        setRows(page.items);
        setTotal(page.total);
        setError(null);
        setTailoring((prev) => {
          const next = new Set(prev);
          for (const row of page.items) {
            // A block leaves status at `discovered` forever (§3.3) — that is not
            // "still tailoring", it's a finished, rejected attempt.
            if (row.status !== "discovered" || row.blocked_reason) next.delete(row.match_id);
          }
          return next;
        });
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    setLoading(true);
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, filter]);

  useEffect(() => {
    if (!userId) return;
    api.getPipelineSummary(userId).then(setSummary).catch(() => setSummary(null));
    api.getProfileByUser(userId).then((p) => setProfileId(p?.id ?? null));
  }, [userId]);

  async function runMatcherNow() {
    if (!profileId) return;
    await api.triggerMatch(profileId);
    setTriggered(true);
  }

  useEffect(() => {
    if (tailoring.size === 0 && !triggered) return;
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tailoring.size, triggered]);

  async function trigger(matchId: string) {
    await api.tailorMatch(matchId);
    setTailoring((prev) => new Set(prev).add(matchId));
  }

  if (!userId) return <div className="text-neutral-500">select a user</div>;

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-neutral-900">Job matches</h1>
        <div className="flex items-center gap-3">
          <span className="text-sm text-neutral-500">
            {summary?.candidates != null
              ? `${(summary.above_threshold ?? 0) + (summary.skipped_below ?? 0)} of ${summary.candidates} candidates cleared the filters`
              : `${total} matches`}
          </span>
          <button
            onClick={runMatcherNow}
            disabled={!profileId || triggered}
            title="Rescores you against every job ingested since the last run. Scraping is on its own 6h schedule."
            className="shrink-0 rounded bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            {triggered ? "fetching…" : "Fetch new jobs"}
          </button>
        </div>
      </div>

      <div className="mb-4 flex gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`rounded-full px-3 py-1 text-sm ${
              filter === f.value
                ? "bg-neutral-900 text-white"
                : "bg-neutral-100 text-neutral-600 hover:bg-neutral-200"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {error && <div className="text-red-600">error: {error}</div>}
      {loading && <div className="text-neutral-500">loading…</div>}

      {!loading && rows.length === 0 && (
        <div className="rounded-lg border border-dashed border-neutral-300 p-8 text-center text-neutral-500">
          <p>no matches yet — the matcher runs every 12h, or press Fetch new jobs</p>
        </div>
      )}

      <div className="divide-y divide-neutral-200 rounded-lg border border-neutral-200 bg-white">
        {rows.map((row) => (
          <div
            key={row.match_id}
            className={`flex items-center gap-4 px-4 py-3 ${
              row.status === "skipped" ? "opacity-50" : ""
            }`}
          >
            <div className="w-12 shrink-0 text-right text-lg font-semibold text-neutral-900">
              {row.score ?? "—"}
            </div>
            <div className="min-w-0 flex-1">
              <Link
                href={`/jobs/${row.match_id}`}
                className="font-medium text-neutral-900 hover:underline"
              >
                {row.job.title}
              </Link>
              <div className="truncate text-sm text-neutral-500">
                {row.job.company} · {row.job.location ?? row.job.remote_mode ?? "—"}
              </div>
              <div className="truncate text-xs text-neutral-400">
                {row.reasons_json?.summary}
              </div>
            </div>
            <span
              title={
                row.blocked_reason
                  ? "The AI-drafted résumé couldn't be verified against your real one, so nothing was generated."
                  : undefined
              }
              className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                row.blocked_reason
                  ? "bg-amber-100 text-amber-800"
                  : (STATUS_STYLE[row.status] ?? "bg-neutral-100 text-neutral-600")
              }`}
            >
              {row.blocked_reason ? "blocked" : row.status}
            </span>
            {row.status === "discovered" ? (
              <button
                onClick={() => trigger(row.match_id)}
                disabled={tailoring.has(row.match_id)}
                title={
                  row.blocked_reason
                    ? "Try generating the résumé again."
                    : undefined
                }
                className="shrink-0 rounded bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              >
                {tailoring.has(row.match_id)
                  ? "tailoring…"
                  : row.blocked_reason
                    ? "Retry tailor"
                    : "Tailor"}
              </button>
            ) : (
              <Link
                href={`/jobs/${row.match_id}`}
                className="shrink-0 rounded border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700"
              >
                View
              </Link>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
