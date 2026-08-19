"use client";

import { useEffect, useState } from "react";
import type { ProfileRead } from "@/lib/types";
import { evidenceCount, skillChips } from "@/lib/resume";

export type FlowStage = "uploading" | "analyzing" | "success" | "error";

// The stages the backend genuinely performs, in order (workers/profiles/parse.py:
// extract text -> LLM parse -> build the evidence vault). There is no per-step signal
// from the backend though — only one "done" or "failed" event at the end — so the
// checklist's *pacing* is a client-side timer, not driven by real progress ticks. The
// last item holds indefinitely (spinner, no checkmark) until the real completion
// signal arrives from the poll in page.tsx. Labels are true to what happens; the
// timing between them is cosmetic.
const STEPS = [
  "Reading your document",
  "Extracting work experience",
  "Identifying skills",
  "Building your profile",
];
const STEP_INTERVAL_MS = 1100;

export function UploadFlowModal({
  stage,
  profile,
  errorMessage,
  onClose,
  onRetry,
}: {
  stage: FlowStage;
  profile: ProfileRead | null;
  errorMessage: string | null;
  onClose: () => void;
  onRetry: () => void;
}) {
  const [visibleSteps, setVisibleSteps] = useState(1);

  useEffect(() => {
    if (stage !== "analyzing") {
      setVisibleSteps(1);
      return;
    }
    if (visibleSteps >= STEPS.length - 1) return; // hold on the last one for real
    const id = setTimeout(() => setVisibleSteps((n) => n + 1), STEP_INTERVAL_MS);
    return () => clearTimeout(id);
  }, [stage, visibleSteps]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
        {stage === "uploading" && <Uploading />}
        {stage === "analyzing" && <Analyzing visibleSteps={visibleSteps} />}
        {stage === "success" && profile && (
          <Success profile={profile} onClose={onClose} />
        )}
        {stage === "error" && (
          <ErrorState message={errorMessage} onRetry={onRetry} onClose={onClose} />
        )}
      </div>
    </div>
  );
}

function Uploading() {
  return (
    <div className="flex flex-col items-center py-6 text-center">
      <Spinner />
      <p className="mt-4 text-sm font-medium text-neutral-800">Uploading your résumé…</p>
    </div>
  );
}

function Analyzing({ visibleSteps }: { visibleSteps: number }) {
  return (
    <div className="py-2">
      <div className="flex flex-col items-center text-center">
        <Spinner />
        <p className="mt-4 text-sm font-medium text-neutral-800">Reading your résumé</p>
        <p className="mt-1 text-xs text-neutral-500">Usually takes 10–30 seconds.</p>
      </div>
      <ul className="mt-5 space-y-2">
        {STEPS.map((step, i) => {
          const done = i < visibleSteps - 1;
          const active = i === visibleSteps - 1;
          if (i >= visibleSteps) return null;
          return (
            <li key={step} className="flex items-center gap-2 text-sm">
              {done ? (
                <span className="text-green-600">✓</span>
              ) : active ? (
                <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
              ) : null}
              <span className={done ? "text-neutral-500" : "font-medium text-neutral-800"}>
                {step}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Success({ profile, onClose }: { profile: ProfileRead; onClose: () => void }) {
  const parsed = profile.parsed_json;
  const chips = skillChips(parsed).slice(0, 8);
  const claims = evidenceCount(parsed);

  return (
    <div className="text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-green-100 text-2xl text-green-600">
        ✓
      </div>
      <h3 className="mt-3 text-lg font-semibold text-neutral-900">
        We read your résumé
      </h3>
      <p className="mt-1 text-sm text-neutral-500">
        {parsed.basics.name ?? "Your profile"} — {profile.seniority ?? "level unset"}
        {parsed.years_experience != null ? ` · ${parsed.years_experience} yrs experience` : ""}
      </p>

      {chips.length > 0 && (
        <div className="mt-4 flex flex-wrap justify-center gap-1.5">
          {chips.map((kw, i) => (
            <span
              key={`${kw}-${i}`}
              className="rounded-full bg-neutral-900 px-2.5 py-1 text-xs font-medium text-white"
            >
              {kw}
            </span>
          ))}
        </div>
      )}

      <p className="mt-3 text-xs text-neutral-400">{claims} claims extracted for tailoring</p>

      <button
        onClick={onClose}
        className="mt-5 w-full rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white"
      >
        View my profile
      </button>
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
  onClose,
}: {
  message: string | null;
  onRetry: () => void;
  onClose: () => void;
}) {
  return (
    <div className="text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-red-100 text-2xl text-red-600">
        !
      </div>
      <h3 className="mt-3 text-lg font-semibold text-neutral-900">The parse didn't finish</h3>
      <p className="mt-1 text-sm text-neutral-600">
        {message ?? "Something went wrong reading this résumé."}
      </p>
      <p className="mt-1 text-xs text-neutral-400">
        Your previous profile, if you had one, is unchanged.
      </p>
      <div className="mt-5 flex gap-2">
        <button
          onClick={onRetry}
          className="flex-1 rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white"
        >
          Try again
        </button>
        <button
          onClick={onClose}
          className="flex-1 rounded border border-neutral-300 px-4 py-2 text-sm text-neutral-700"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <div className="h-8 w-8 animate-spin rounded-full border-4 border-neutral-200 border-t-neutral-900" />
  );
}
