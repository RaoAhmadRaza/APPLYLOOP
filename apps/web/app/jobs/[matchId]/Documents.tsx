"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { PipelineRow } from "@/lib/types";

const LABEL: Record<string, string> = {
  resume: "Résumé",
  cover_letter: "Cover letter",
};

function Stat({ label, value }: { label: string; value: number | null }) {
  return (
    <div>
      <div className="font-medium text-neutral-700">{value ?? "—"}</div>
      <div>{label}</div>
    </div>
  );
}

export function Documents({ row }: { row: PipelineRow }) {
  const [preview, setPreview] = useState<string | null>(null);
  const resume = row.documents.find((d) => d.type === "resume");
  const letter = row.documents.find((d) => d.type === "cover_letter");

  if (row.status === "discovered") return null;

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-6">
      <h3 className="mb-3 text-sm font-medium uppercase text-neutral-500">Documents</h3>
      <div className="space-y-2">
        <DocCard
          label={LABEL.resume}
          doc={resume}
          onOpen={setPreview}
          missingText="not written"
        />
        <DocCard
          label={LABEL.cover_letter}
          doc={letter}
          onOpen={setPreview}
          missingText="not written — could not be grounded in the vault"
        />
      </div>

      {(row.keywords_matched != null || row.keywords_total != null) && (
        <p className="mt-3 text-xs text-neutral-500">
          ATS keyword coverage: {row.keywords_matched} / {row.keywords_total}
        </p>
      )}

      {row.bullets_kept != null && (
        <div className="mt-3 grid grid-cols-4 gap-2 border-t border-neutral-100 pt-3 text-xs text-neutral-500">
          <Stat label="bullets kept" value={row.bullets_kept} />
          <Stat label="bullets stripped" value={row.bullets_stripped} />
          <Stat label="skills kept" value={row.skills_kept} />
          <Stat label="fabricated skills" value={row.fabricated_skills} />
        </div>
      )}
      {row.generated_at && (
        <p className="mt-2 text-xs text-neutral-400">
          generated {new Date(row.generated_at).toLocaleString()}
        </p>
      )}

      {preview && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-8"
          onClick={() => setPreview(null)}
        >
          <div
            className="h-full w-full max-w-3xl rounded-lg bg-white p-2 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <iframe src={preview} className="h-full w-full rounded" title="document preview" />
          </div>
        </div>
      )}
    </section>
  );
}

function DocCard({
  label,
  doc,
  onOpen,
  missingText,
}: {
  label: string;
  doc: PipelineRow["documents"][number] | undefined;
  onOpen: (url: string) => void;
  missingText: string;
}) {
  const [downloading, setDownloading] = useState(false);

  if (!doc) {
    return (
      <div className="flex items-center justify-between rounded border border-dashed border-neutral-200 px-3 py-2 text-sm text-neutral-400">
        <span>{label}</span>
        <span>{missingText}</span>
      </div>
    );
  }
  const url = api.downloadUrl(doc.id);

  // A plain <a href download> doesn't reliably force a save across the presigned-URL
  // redirect (the `download` attribute only binds same-origin) — fetch the bytes and
  // hand the browser a local blob URL instead, same trick the extension already uses
  // to pull these same documents (popup.js's loadDocuments).
  async function download() {
    setDownloading(true);
    try {
      const res = await fetch(url);
      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = `${label.toLowerCase().replace(/\s+/g, "-")}-v${doc?.version}.pdf`;
      a.click();
      URL.revokeObjectURL(blobUrl);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="flex items-center justify-between rounded border border-neutral-200 px-3 py-2 text-sm">
      <span className="font-medium text-neutral-800">
        {label} <span className="text-neutral-400">v{doc.version}</span>
      </span>
      <div className="flex gap-3">
        <button onClick={() => onOpen(url)} className="text-blue-600 hover:underline">
          Open PDF
        </button>
        <button
          onClick={download}
          disabled={downloading}
          className="text-blue-600 hover:underline disabled:opacity-50"
        >
          {downloading ? "downloading…" : "Download"}
        </button>
        {doc.gdrive_url && (
          <a
            href={doc.gdrive_url}
            target="_blank"
            rel="noreferrer"
            className="text-blue-600 hover:underline"
          >
            Drive
          </a>
        )}
      </div>
    </div>
  );
}
