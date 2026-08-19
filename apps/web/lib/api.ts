import type {
  ApplicationRead,
  JobRead,
  MatchRead,
  Page,
  ParseStatus,
  PipelineRow,
  PipelineSummary,
  ProfileRead,
  SuggestedPrefs,
  UserRead,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  listUsers: () => request<Page<UserRead>>("/users?limit=100"),

  getProfileByUser: async (userId: string): Promise<ProfileRead | null> => {
    // No "get profile by user id" route — profiles is generic CRUD keyed by
    // profile id. One user -> one profile (§6.2), so list + filter.
    const page = await request<Page<ProfileRead>>("/profiles?limit=100");
    return page.items.find((p) => p.user_id === userId) ?? null;
  },

  createProfile: (userId: string) =>
    request<ProfileRead>("/profiles", {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),

  patchProfile: (profileId: string, body: Record<string, unknown>) =>
    request<ProfileRead>(`/profiles/${profileId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  uploadResume: async (profileId: string, file: File): Promise<ProfileRead> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}/profiles/${profileId}/resume`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new ApiError(res.status, body || res.statusText);
    }
    return res.json();
  },

  getPipeline: (userId: string, status?: string) =>
    request<Page<PipelineRow>>(
      `/users/${userId}/pipeline?limit=100${status ? `&status=${status}` : ""}`,
    ),

  // No single-match pipeline read exists, so the detail screen re-uses the list
  // and filters client-side. Fine at demo scale; a `GET /matches/{id}/pipeline`
  // would be the fix if this ever needs to scale past one page.
  getPipelineRow: async (userId: string, matchId: string): Promise<PipelineRow | null> => {
    const page = await request<Page<PipelineRow>>(`/users/${userId}/pipeline?limit=100`);
    return page.items.find((r) => r.match_id === matchId) ?? null;
  },

  getPipelineSummary: (userId: string) =>
    request<PipelineSummary>(`/users/${userId}/pipeline/summary`),

  getParseStatus: (profileId: string) =>
    request<ParseStatus>(`/profiles/${profileId}/parse-status`),

  triggerMatch: (profileId: string) =>
    request<unknown>(`/profiles/${profileId}/match`, { method: "POST" }),

  triggerSuggestPrefs: (profileId: string) =>
    request<unknown>(`/profiles/${profileId}/suggest-prefs`, { method: "POST" }),

  getSuggestedPrefs: (profileId: string) =>
    request<SuggestedPrefs>(`/profiles/${profileId}/suggested-prefs`),

  getJob: (jobId: string) => request<JobRead>(`/jobs/${jobId}`),

  getMatch: (matchId: string) => request<MatchRead>(`/matches/${matchId}`),

  approveMatch: (matchId: string) =>
    request<unknown>(`/matches/${matchId}/approve`, { method: "POST" }),

  skipMatch: (matchId: string) =>
    request<unknown>(`/matches/${matchId}/skip`, { method: "POST" }),

  tailorMatch: (matchId: string) =>
    request<unknown>(`/matches/${matchId}/tailor`, { method: "POST" }),

  downloadUrl: (documentId: string) => `${API_BASE}/documents/${documentId}/download`,

  listApplications: () => request<Page<ApplicationRead>>("/applications?limit=100"),
};
