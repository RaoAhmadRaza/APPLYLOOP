"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ProfileRead, SuggestedPrefs } from "@/lib/types";

// List fields are edited as comma-separated text — a tag editor is more polish
// than a demo needs. `Prefs` (packages/schemas/prefs.py) is the wire contract.
function toList(v: string): string[] {
  return v
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function PrefsForm({
  profile,
  onSaved,
  suggested,
  suggesting,
}: {
  profile: ProfileRead;
  onSaved: (p: ProfileRead) => void;
  // A model's proposed starting values for a résumé just parsed — pre-fills the
  // boxes below (overwriting whatever was there, since this fires right after a
  // meaningfully different résumé landed). Nothing here is saved until the user
  // clicks save; skip it by just clicking save on the pre-filled values as-is, or
  // edit first. `salary_floor` and `work_auth` aren't suggested — the former is too
  // speculative to have a model guess at, the latter is a promoted column already
  // derived straight from the résumé parse, not a search preference to propose.
  suggested?: SuggestedPrefs | null;
  suggesting?: boolean;
}) {
  const prefs = profile.prefs_json;
  const [titles, setTitles] = useState(prefs.titles.join(", "));
  const [locations, setLocations] = useState(profile.locations.join(", "));
  const [remoteModes, setRemoteModes] = useState(prefs.remote_modes.join(", "));
  const [salaryFloor, setSalaryFloor] = useState(profile.salary_floor?.toString() ?? "");
  const [workAuth, setWorkAuth] = useState(profile.work_auth ?? "");
  const [mustHave, setMustHave] = useState(prefs.must_have_keywords.join(", "));
  const [exclude, setExclude] = useState(prefs.exclude_keywords.join(", "));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [justSuggested, setJustSuggested] = useState(false);

  useEffect(() => {
    if (!suggested) return;
    setTitles(suggested.titles.join(", "));
    setLocations(suggested.locations.join(", "));
    setRemoteModes(suggested.remote_modes.join(", "));
    setMustHave(suggested.must_have_keywords.join(", "));
    setExclude(suggested.exclude_keywords.join(", "));
    setJustSuggested(true);
  }, [suggested]);

  async function save() {
    setSaving(true);
    setSaved(false);
    try {
      const updated = await api.patchProfile(profile.id, {
        locations: toList(locations),
        salary_floor: salaryFloor ? Number(salaryFloor) : null,
        work_auth: workAuth || null,
        prefs_json: {
          titles: toList(titles),
          remote_modes: toList(remoteModes),
          must_have_keywords: toList(mustHave),
          exclude_keywords: toList(exclude),
        },
      });
      onSaved(updated);
      setSaved(true);
      setJustSuggested(false);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-6">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-medium uppercase text-neutral-500">Preferences</h3>
        {suggesting && (
          <span className="flex items-center gap-1.5 text-xs text-neutral-400">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />
            getting AI suggestions…
          </span>
        )}
      </div>

      {justSuggested && (
        <div className="mb-4 rounded border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800">
          Pre-filled from your résumé by DeepSeek. Review and save, edit anything first,
          or leave as-is — nothing is saved until you click save.
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <Field label="Target titles" value={titles} onChange={setTitles} />
        <Field label="Locations" value={locations} onChange={setLocations} />
        <Field
          label="Remote modes (remote, hybrid, onsite)"
          value={remoteModes}
          onChange={setRemoteModes}
        />
        <Field label="Salary floor" value={salaryFloor} onChange={setSalaryFloor} type="number" />
        <Field label="Work auth" value={workAuth} onChange={setWorkAuth} />
        <Field label="Must-have keywords" value={mustHave} onChange={setMustHave} />
        <Field label="Exclude keywords" value={exclude} onChange={setExclude} />
      </div>
      <button
        onClick={save}
        disabled={saving}
        className="mt-4 rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
      >
        {saving ? "saving…" : "save"}
      </button>
      {saved && <span className="ml-3 text-sm text-green-600">saved</span>}
    </section>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-neutral-500">{label}</span>
      <input
        type={type}
        className="w-full rounded border border-neutral-300 px-2 py-1.5 text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}
