"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useUser } from "./UserContext";

const NAV = [
  { href: "/profile", label: "Profile" },
  { href: "/jobs", label: "Jobs" },
  { href: "/applications", label: "Applications" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { users, userId, setUserId, loading } = useUser();
  const [lastRunAt, setLastRunAt] = useState<string | null>(null);

  useEffect(() => {
    if (!userId) return;
    api
      .getPipelineSummary(userId)
      .then((s) => setLastRunAt(s.last_run_at))
      .catch(() => setLastRunAt(null));
  }, [userId]);

  return (
    <div className="flex min-h-screen">
      <aside className="w-56 shrink-0 border-r border-neutral-200 bg-neutral-50 p-4">
        <div className="mb-6 text-lg font-semibold text-neutral-900">ApplyLoop</div>
        <nav className="flex flex-col gap-1">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`rounded px-3 py-2 text-sm ${
                pathname?.startsWith(item.href)
                  ? "bg-neutral-900 text-white"
                  : "text-neutral-700 hover:bg-neutral-200"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="mt-8">
          <label className="mb-1 block text-xs font-medium uppercase text-neutral-500">
            User
          </label>
          {loading ? (
            <div className="text-sm text-neutral-400">loading…</div>
          ) : (
            <select
              className="w-full rounded border border-neutral-300 bg-white px-2 py-1.5 text-sm"
              value={userId ?? ""}
              onChange={(e) => setUserId(e.target.value)}
            >
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.email}
                </option>
              ))}
            </select>
          )}
        </div>

        {lastRunAt && (
          <div className="mt-8 text-xs text-neutral-400">
            last pipeline run
            <br />
            {new Date(lastRunAt).toLocaleString()}
          </div>
        )}
      </aside>
      <main className="flex-1 overflow-y-auto p-8">{children}</main>
    </div>
  );
}
