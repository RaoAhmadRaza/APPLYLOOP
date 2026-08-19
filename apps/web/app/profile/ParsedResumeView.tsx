import type { ProfileRead } from "@/lib/types";
import { evidenceCount as countEvidence, skillChips as chips } from "@/lib/resume";

export function ParsedResumeView({ profile }: { profile: ProfileRead }) {
  const parsed = profile.parsed_json;
  const basics = parsed.basics;
  // Screen 1's note: the skills chips are the moment the audience believes it
  // actually read the document. One chip per keyword, split at parse time (§4b.1) —
  // never one giant "Languages: Python, Go, SQL" blob.
  const skillChips = chips(parsed);
  const evidenceCount = countEvidence(parsed);;

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-6">
      <div className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold text-neutral-900">{basics.name ?? "—"}</h2>
        <span className="text-sm text-neutral-500">{basics.label}</span>
      </div>
      <div className="mt-1 text-sm text-neutral-500">
        {[basics.email, basics.phone, basics.location?.city].filter(Boolean).join(" · ")}
      </div>

      {skillChips.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-1.5">
          {skillChips.map((kw, i) => (
            <span
              key={`${kw}-${i}`}
              className="rounded-full bg-neutral-900 px-3 py-1 text-xs font-medium text-white"
            >
              {kw}
            </span>
          ))}
        </div>
      )}

      <div className="mt-6 grid grid-cols-5 gap-4 text-sm">
        <Fact label="Seniority" value={profile.seniority} />
        <Fact label="Years experience" value={parsed.years_experience ?? undefined} />
        <Fact label="Work auth" value={profile.work_auth} />
        <Fact label="Salary floor" value={profile.salary_floor} />
        <Fact label="Evidence claims" value={evidenceCount || undefined} />
      </div>

      {parsed.work.length > 0 && (
        <div className="mt-6">
          <h3 className="mb-2 text-sm font-medium uppercase text-neutral-500">Experience</h3>
          <div className="space-y-4">
            {parsed.work.map((role, i) => (
              <div key={i}>
                <div className="flex items-baseline justify-between">
                  <span className="font-medium text-neutral-900">
                    {role.position} · {role.name}
                  </span>
                  <span className="text-xs text-neutral-500">
                    {role.start_date} – {role.end_date ?? "present"}
                  </span>
                </div>
                {role.highlights.length > 0 && (
                  <ul className="mt-1 list-disc pl-5 text-sm text-neutral-600">
                    {role.highlights.map((h, j) => (
                      <li key={j}>{h}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {parsed.education.length > 0 && (
        <div className="mt-6">
          <h3 className="mb-2 text-sm font-medium uppercase text-neutral-500">Education</h3>
          <ul className="space-y-1 text-sm text-neutral-700">
            {parsed.education.map((edu, i) => (
              <li key={i}>
                {edu.study_type} {edu.area} — {edu.institution}
              </li>
            ))}
          </ul>
        </div>
      )}

      {parsed.certificates.length > 0 && (
        <div className="mt-6">
          <h3 className="mb-2 text-sm font-medium uppercase text-neutral-500">
            Certifications
          </h3>
          <ul className="space-y-1 text-sm text-neutral-700">
            {parsed.certificates.map((c, i) => (
              <li key={i}>
                {c.name} — {c.issuer}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div>
      <div className="text-xs uppercase text-neutral-400">{label}</div>
      <div className="text-neutral-800">{value ?? "—"}</div>
    </div>
  );
}
