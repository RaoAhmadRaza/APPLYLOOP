"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ParseStatus, ProfileRead, SuggestedPrefs } from "@/lib/types";
import { useUser } from "@/components/UserContext";
import { UploadZone } from "./UploadZone";
import { ParsedResumeView } from "./ParsedResumeView";
import { PrefsForm } from "./PrefsForm";
import { UploadFlowModal, type FlowStage } from "./UploadFlowModal";

const POLL_MS = 3000;
// A soft ceiling on the "analyzing" poll, not a hard failure — the upload already
// landed either way. Past this we stop pretending the modal knows something it
// doesn't and tell the user to check back, instead of spinning forever.
const MAX_POLLS = 40; // ~2 minutes at POLL_MS

export default function ProfilePage() {
  const { userId } = useUser();
  const [profile, setProfile] = useState<ProfileRead | null>(null);
  const [parseStatus, setParseStatus] = useState<ParseStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // The upload flow, entirely separate from the page's own load/refresh above.
  const [flowStage, setFlowStage] = useState<FlowStage | null>(null);
  const [flowError, setFlowError] = useState<string | null>(null);
  // Watermark: the profile's own `updated_at` at the moment the upload committed.
  // The upload endpoint bumps `updated_at` itself (it writes resume_url), so "has a
  // parse happened since" has to be "a LATER updated_at than that", not "is
  // parsed_json non-empty" — a re-upload over an already-parsed profile leaves the
  // OLD parsed_json sitting there until the new parse lands, and that stale
  // non-empty value was silently read as "already done, nothing to wait for". That
  // was the bug: uploading over an existing profile looked like it did nothing.
  const pendingSinceRef = useRef<string | null>(null);

  // Suggested preferences, proposed by a model right after a fresh parse — never
  // auto-saved (see PrefsSuggestion's docstring). Same watermark trick as the parse
  // poll above: capture whatever suggestion existed (or null) right before asking
  // for a new one, then wait for `.at` to move past it, rather than trusting
  // non-null as "the new one" (a previous session's suggestion would already be
  // non-null).
  const [suggestedPrefs, setSuggestedPrefs] = useState<SuggestedPrefs | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const suggestionWatermarkRef = useRef<string | null>(null);

  const load = useCallback(async () => {
    if (!userId) return;
    try {
      const p = await api.getProfileByUser(userId);
      setProfile(p);
      setParseStatus(p ? await api.getParseStatus(p.id) : null);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  async function handleUpload(file: File) {
    if (!userId) return;
    setFlowStage("uploading");
    setFlowError(null);
    try {
      let target = profile;
      if (!target) target = await api.createProfile(userId);
      const uploaded = await api.uploadResume(target.id, file);
      setProfile(uploaded);
      pendingSinceRef.current = uploaded.updated_at;
      setFlowStage("analyzing");
    } catch (e) {
      setFlowError(e instanceof Error ? e.message : String(e));
      setFlowStage("error");
    }
  }

  useEffect(() => {
    if (flowStage !== "analyzing" || !userId) return;
    let cancelled = false;
    let polls = 0;

    const id = setInterval(async () => {
      polls += 1;
      const watermark = pendingSinceRef.current;
      if (!watermark) return;

      const fresh = await api.getProfileByUser(userId);
      if (cancelled || !fresh) return;
      const status = await api.getParseStatus(fresh.id);
      if (cancelled) return;

      // A real per-profile failure (§3.7's audit trail), newer than this attempt.
      if (status.failed && status.at && status.at > watermark) {
        setProfile(fresh);
        setParseStatus(status);
        setFlowError(status.error);
        setFlowStage("error");
        pendingSinceRef.current = null;
        return;
      }
      // A real success: the row was written again (parsed_json included) after the
      // upload's own commit, which is what `updated_at` moving past the watermark
      // means.
      if (fresh.updated_at > watermark && Object.keys(fresh.parsed_json ?? {}).length > 0) {
        setProfile(fresh);
        setParseStatus(status);
        setFlowStage("success");
        pendingSinceRef.current = null;
        void startFollowUps(fresh);
        return;
      }
      if (polls >= MAX_POLLS) {
        setFlowError(
          "This is taking longer than expected. Your upload was saved — check back in a bit.",
        );
        setFlowStage("error");
        pendingSinceRef.current = null;
      }
    }, POLL_MS);

    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [flowStage, userId]);

  // Fires once, right when a fresh parse lands: (1) the job pool is scored against
  // the OLD résumé until something re-matches — a résumé change with no new matches
  // to show for it is a dead end most job tools handle by re-running the matcher
  // automatically. (2) ask for suggested search preferences from the new content.
  // Both are fire-and-forget triggers on endpoints that already exist for manual use
  // (Jobs screen's "Run the matcher now", and this one) — this just also fires them
  // on the moment a human would expect it, without waiting to be asked.
  async function startFollowUps(freshProfile: ProfileRead) {
    const before = await api.getSuggestedPrefs(freshProfile.id).catch(() => null);
    suggestionWatermarkRef.current = before?.at ?? null;

    api.triggerMatch(freshProfile.id).catch(() => {});
    api.triggerSuggestPrefs(freshProfile.id).catch(() => {});
    setSuggesting(true);
  }

  useEffect(() => {
    if (!suggesting || !profile) return;
    let cancelled = false;
    let tries = 0;
    const MAX_TRIES = 12; // ~24s — this call is one short LLM round trip, not a parse

    const id = setInterval(async () => {
      tries += 1;
      const fresh = await api.getSuggestedPrefs(profile.id).catch(() => null);
      if (cancelled || !fresh) return;
      if (fresh.at && fresh.at !== suggestionWatermarkRef.current) {
        setSuggestedPrefs(fresh);
        setSuggesting(false);
        return;
      }
      if (tries >= MAX_TRIES) setSuggesting(false); // give up quietly — the form still works unprefilled
    }, 2000);

    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [suggesting, profile]);

  if (loading) return <div className="text-neutral-500">loading…</div>;
  if (!userId) return <div className="text-neutral-500">select a user</div>;
  if (error) return <div className="text-red-600">error: {error}</div>;

  const hasResume = !!profile?.resume_url || !!profile?.master_resume;
  const hasParsed = Object.keys(profile?.parsed_json ?? {}).length > 0;

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <h1 className="text-2xl font-semibold text-neutral-900">Profile &amp; résumé</h1>

      {parseStatus?.failed && flowStage === null && (
        <section className="rounded-lg border border-red-300 bg-red-50 p-4">
          <div className="text-sm font-medium text-red-900">The last résumé parse failed</div>
          <p className="mt-1 text-sm text-red-800">{parseStatus.error}</p>
          <p className="mt-1 text-xs text-red-700">
            Your previous profile below is unchanged — nothing was overwritten with an
            empty parse. Try uploading again.
          </p>
        </section>
      )}

      <section className="rounded-lg border border-neutral-200 bg-white p-6">
        <UploadZone onUpload={handleUpload} hasResume={hasResume} />
      </section>

      {hasParsed && profile && (
        <>
          <ParsedResumeView profile={profile} />
          <PrefsForm
            profile={profile}
            onSaved={(p) => {
              setProfile(p);
              setSuggestedPrefs(null); // consumed — don't keep re-offering a stale one
            }}
            suggested={suggestedPrefs}
            suggesting={suggesting}
          />
        </>
      )}

      {flowStage && (
        <UploadFlowModal
          stage={flowStage}
          profile={profile}
          errorMessage={flowError}
          onClose={() => setFlowStage(null)}
          onRetry={() => setFlowStage(null)}
        />
      )}
    </div>
  );
}
