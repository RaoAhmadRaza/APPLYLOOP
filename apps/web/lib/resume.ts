import type { ParsedResume } from "./types";

// Shared between ParsedResumeView (screen 1's full view) and UploadFlowModal (the
// upload flow's success card) so the two numbers can't drift apart.

export function skillChips(parsed: ParsedResume): string[] {
  return parsed.skills.flatMap((s) => (s.keywords.length ? s.keywords : s.name ? [s.name] : []));
}

// No /evidence route exists yet (DEMO_PLAN's own note names this fallback: "show the
// count from parsed_json"). Approximates the vault's four claim kinds — skill, bullet,
// title, credential — from what the parse already returned.
export function evidenceCount(parsed: ParsedResume): number {
  return (
    skillChips(parsed).length +
    parsed.work.reduce((sum, role) => sum + role.highlights.length, 0) +
    parsed.work.length +
    parsed.certificates.length
  );
}
