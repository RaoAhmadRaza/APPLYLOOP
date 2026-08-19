"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { ApplicationRead } from "@/lib/types";

interface JoinedApplication extends ApplicationRead {
  company: string | null;
  title: string | null;
}

// No `GET /applications` join exists — client-side join over the existing generic
// `/matches/{id}` and `/jobs/{id}` reads. Fine at demo scale (a handful of rows); if
// this list ever needs to scale, a bespoke joined read (matching pipeline.py's own
// pattern) is the fix, not more client-side fan-out.
async function join(row: ApplicationRead): Promise<JoinedApplication> {
  try {
    const match = await api.getMatch(row.match_id);
    const job = await api.getJob(match.job_id);
    return { ...row, company: job.company, title: job.title };
  } catch {
    return { ...row, company: null, title: null };
  }
}

export default function ApplicationsPage() {
  const [rows, setRows] = useState<JoinedApplication[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listApplications()
      .then((p) => Promise.all(p.items.map(join)))
      .then(setRows)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="mb-4 text-2xl font-semibold text-neutral-900">Applications</h1>

      {loading && <div className="text-neutral-500">loading…</div>}
      {error && <div className="text-red-600">error: {error}</div>}

      {!loading && rows.length === 0 && (
        <div className="rounded-lg border border-dashed border-neutral-300 p-8 text-center text-neutral-500">
          nothing submitted yet
        </div>
      )}

      {rows.length > 0 && (
        <table className="w-full border-collapse overflow-hidden rounded-lg border border-neutral-200 bg-white text-sm">
          <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-500">
            <tr>
              <th className="px-4 py-2">Company</th>
              <th className="px-4 py-2">Title</th>
              <th className="px-4 py-2">Method</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Submitted</th>
              <th className="px-4 py-2">Confirmation</th>
              <th className="px-4 py-2">Error</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100">
            {rows.map((row) => (
              <tr key={row.id}>
                <td className="px-4 py-2">{row.company ?? "—"}</td>
                <td className="px-4 py-2">{row.title ?? "—"}</td>
                <td className="px-4 py-2">{row.method}</td>
                <td className="px-4 py-2">{row.status}</td>
                <td className="px-4 py-2">
                  {row.submitted_at ? new Date(row.submitted_at).toLocaleString() : "—"}
                </td>
                <td className="px-4 py-2">{row.confirmation ?? "—"}</td>
                <td className="max-w-xs whitespace-pre-wrap px-4 py-2 text-red-600">
                  {row.error ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
