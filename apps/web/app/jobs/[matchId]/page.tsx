"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { JobRead, PipelineRow } from "@/lib/types";
import { useUser } from "@/components/UserContext";
import { Verdict } from "./Verdict";
import { Documents } from "./Documents";

export default function MatchDetailPage() {
  const { matchId } = useParams<{ matchId: string }>();
  const { userId } = useUser();
  const [row, setRow] = useState<PipelineRow | null>(null);
  const [job, setJob] = useState<JobRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    if (!userId) return;
    try {
      const r = await api.getPipelineRow(userId, matchId);
      setRow(r);
      if (r) setJob(await api.getJob(r.job.id));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setLoading(true);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, matchId]);

  // Poll while a tailor request is in flight (status stays `discovered` until the
  // worker finishes — same reasoning as the jobs list).
  const [tailoring, setTailoring] = useState(false);
  useEffect(() => {
    if (!tailoring) return;
    const id = setInterval(async () => {
      await load();
    }, 4000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tailoring]);
  useEffect(() => {
    // A block leaves status at `discovered` forever (§3.3) — that is a finished,
    // rejected attempt, not "still tailoring".
    if (row && (row.status !== "discovered" || row.blocked_reason)) setTailoring(false);
  }, [row]);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className="text-neutral-500">loading…</div>;
  if (error) return <div className="text-red-600">error: {error}</div>;
  if (!row || !job) return <div className="text-neutral-500">match not found</div>;

  return (
    <div className="mx-auto grid max-w-6xl grid-cols-2 gap-8">
      <section className="space-y-3">
        <h1 className="text-xl font-semibold text-neutral-900">{job.title}</h1>
        <div className="text-neutral-600">
          {job.company} · {job.location ?? job.remote_mode ?? "—"}
        </div>
        <div className="text-sm text-neutral-400">
          posted {job.posted_at ? new Date(job.posted_at).toLocaleDateString() : "—"}
        </div>
        <a
          href={job.url}
          target="_blank"
          rel="noreferrer"
          className="inline-block text-sm text-blue-600 hover:underline"
        >
          view the real posting ↗
        </a>
        <JobDescription description={job.description} />
      </section>

      <section className="space-y-6">
        <Verdict row={row} />
        {row.blocked_reason && <BlockedPanel reason={row.blocked_reason} />}
        <Documents row={row} />

        <div className="flex items-center gap-2 border-t border-neutral-200 pt-4">
          {row.status === "discovered" && (
            <button
              disabled={busy || tailoring}
              onClick={() =>
                act(async () => {
                  await api.tailorMatch(row.match_id);
                  setTailoring(true);
                })
              }
              className="rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {tailoring ? "tailoring…" : row.blocked_reason ? "Retry tailor" : "Tailor now"}
            </button>
          )}

          {row.status === "tailored" && (
            <button
              disabled={busy}
              onClick={() => act(() => api.approveMatch(row.match_id))}
              className="rounded bg-green-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              Approve
            </button>
          )}

          {(row.status === "approved" || row.status === "applied") && (
            <span className="flex items-center gap-1.5 rounded bg-green-50 px-3 py-2 text-sm font-medium text-green-700">
              ✓ {row.status === "applied" ? "Applied" : "Approved"}
            </span>
          )}

          {row.status === "skipped" && (
            <span className="rounded bg-neutral-100 px-3 py-2 text-sm font-medium text-neutral-500">
              Skipped
            </span>
          )}

          {row.status !== "skipped" && row.status !== "applied" && (
            <button
              disabled={busy}
              onClick={() => act(() => api.skipMatch(row.match_id))}
              className="rounded border border-neutral-300 px-4 py-2 text-sm text-neutral-700 disabled:opacity-50"
            >
              Skip
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

// DEMO_PLAN §6, screen 3: "the 'blocked' state is worth having a real example of.
// A document the system refused to ship is the most convincing screen in the
// product." — the fabrication validator did its job; this is not an error.
//
// `reason` is the backend's own diagnostic string, written for a log line, not a
// user (workers/tailoring/validate.py:339 — "N% of bullets were untraceable,
// ceiling M%"). Translated here to what actually happened, with the raw string
// kept for anyone who wants it.
const STRIP_RATE = /^(\d+)% of bullets were untraceable, ceiling (\d+)%$/;

function explainBlock(reason: string): string {
  const match = STRIP_RATE.exec(reason);
  if (!match) return "The AI-written résumé made claims it couldn't back up with your résumé, so nothing was generated.";
  const [, rate] = match;
  return rate === "100"
    ? "None of the bullet points the AI drafted could be traced back to anything in your résumé, so it refused to generate one rather than invent your work history."
    : `${rate}% of the bullet points the AI drafted couldn't be traced back to your résumé, more than this system allows, so it refused to generate one rather than risk inventing part of your work history.`;
}

function BlockedPanel({ reason }: { reason: string }) {
  return (
    <section className="rounded-lg border border-amber-300 bg-amber-50 p-4">
      <div className="text-sm font-medium text-amber-900">
        Blocked — no résumé was generated for this job
      </div>
      <p className="mt-1 text-sm text-amber-800">{explainBlock(reason)}</p>
      <p className="mt-2 text-xs text-amber-700">
        This is the safety check working as intended: ApplyLoop never puts a claim on
        your résumé it can't verify against the original. Click "Retry tailor" to try
        again, or Skip this job.
      </p>
      <details className="mt-2 text-xs text-amber-600">
        <summary className="cursor-pointer">technical detail</summary>
        <span className="mt-1 block">{reason}</span>
      </details>
    </section>
  );
}

function JobDescription({ description }: { description: string | null }) {
  const [expanded, setExpanded] = useState(false);
  if (!description) return null;
  const lines = description.split("\n");
  const collapsed = lines.length > 15 && !expanded;
  return (
    <div>
      <div
        className={`whitespace-pre-wrap text-sm text-neutral-700 ${
          collapsed ? "line-clamp-[15] overflow-hidden" : ""
        }`}
      >
        {description}
      </div>
      {lines.length > 15 && (
        <button
          className="mt-1 text-xs text-blue-600 hover:underline"
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? "show less" : "show more"}
        </button>
      )}
    </div>
  );
}
