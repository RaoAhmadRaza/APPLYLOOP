// Mirrors packages/schemas — only the fields the dashboard reads.

export interface UserRead {
  id: string;
  email: string;
  auth_id: string;
  plan: string;
  created_at: string;
}

export interface ResumeLocation {
  city: string | null;
  region: string | null;
  country_code: string | null;
}

export interface ResumeBasics {
  name: string | null;
  label: string | null;
  email: string | null;
  phone: string | null;
  url: string | null;
  summary: string | null;
  location: ResumeLocation | null;
}

export interface ResumeWork {
  name: string | null;
  position: string | null;
  location: string | null;
  start_date: string | null;
  end_date: string | null;
  summary: string | null;
  highlights: string[];
}

export interface ResumeEducation {
  institution: string | null;
  area: string | null;
  study_type: string | null;
  start_date: string | null;
  end_date: string | null;
}

export interface ResumeSkill {
  name: string | null;
  keywords: string[];
}

export interface ResumeCertificate {
  name: string | null;
  issuer: string | null;
  date: string | null;
}

export interface ParsedResume {
  basics: ResumeBasics;
  work: ResumeWork[];
  education: ResumeEducation[];
  skills: ResumeSkill[];
  certificates: ResumeCertificate[];
  years_experience: number | null;
  work_authorization: string | null;
}

export interface Prefs {
  remote_modes: string[];
  must_have_keywords: string[];
  exclude_keywords: string[];
  titles: string[];
}

// From /profiles/{id}/suggested-prefs — a proposal to pre-fill the form, never
// auto-saved. `locations` mirrors Prefs.titles's shape but isn't part of Prefs itself
// (locations live on the promoted `profiles.locations` column, not in prefs_json).
export interface SuggestedPrefs {
  titles: string[];
  locations: string[];
  remote_modes: string[];
  must_have_keywords: string[];
  exclude_keywords: string[];
  at: string | null;
}

export interface ProfileRead {
  id: string;
  user_id: string;
  master_resume: string | null;
  resume_url: string | null;
  parsed_json: ParsedResume;
  prefs_json: Prefs;
  work_auth: string | null;
  work_auth_regions: string[] | null;
  locations: string[];
  seniority: string | null;
  salary_floor: number | null;
  created_at: string;
  updated_at: string;
}

export interface JobRead {
  id: string;
  source: string;
  external_id: string;
  title: string;
  company: string;
  location: string | null;
  locations: string[];
  remote_mode: string | null;
  description: string | null;
  url: string;
  ats_type: string | null;
  posted_at: string | null;
  closed_at: string | null;
}

export interface PipelineJob {
  id: string;
  title: string;
  company: string;
  location: string | null;
  remote_mode: string | null;
  url: string | null;
  ats_type: string | null;
  posted_at: string | null;
}

export interface PipelineDocument {
  id: string;
  type: "resume" | "cover_letter";
  version: number;
  gdrive_url: string | null;
}

export interface MatchReasons {
  summary: string;
  met: string[];
  missing: string[];
  disqualifiers: string[];
  bars: string[];
  coverage: number | null;
  similarity: number;
  seniority_delta: number | null;
  filters_passed: string[];
  threshold: number;
  model: string;
  embed_model: string;
}

export type MatchStatus =
  | "discovered"
  | "tailored"
  | "queued"
  | "approved"
  | "applied"
  | "skipped";

export interface PipelineRow {
  match_id: string;
  score: number | null;
  label: string | null;
  status: MatchStatus;
  created_at: string;
  reasons_json: MatchReasons;
  job: PipelineJob;
  documents: PipelineDocument[];
  keywords_matched: number | null;
  keywords_total: number | null;
  bullets_kept: number | null;
  bullets_stripped: number | null;
  skills_kept: number | null;
  fabricated_skills: number | null;
  generated_at: string | null;
  // Set when the fabrication validator refused the last tailor attempt. The match
  // stays `discovered` forever in that case (CLAUDE.md §3.3), so this is the only
  // signal that a tailor was tried and rejected rather than never attempted.
  blocked_reason: string | null;
}

export interface PipelineSummary {
  last_run_at: string | null;
  candidates: number | null;
  embedded_new: number | null;
  shortlisted: number | null;
  above_threshold: number | null;
  skipped_below: number | null;
}

export interface ParseStatus {
  failed: boolean;
  error: string | null;
  at: string | null;
}

export interface MatchRead {
  id: string;
  user_id: string;
  job_id: string;
  score: number | null;
  label: string | null;
  status: MatchStatus;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApplicationRead {
  id: string;
  match_id: string;
  method: "agent" | "extension" | "manual";
  status: string;
  submitted_at: string | null;
  confirmation: string | null;
  error: string | null;
}
