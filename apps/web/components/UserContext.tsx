"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { UserRead } from "@/lib/types";

interface UserContextValue {
  users: UserRead[];
  userId: string | null;
  setUserId: (id: string) => void;
  loading: boolean;
}

const UserContext = createContext<UserContextValue | null>(null);

const STORAGE_KEY = "applyloop.userId";

export function UserProvider({ children }: { children: React.ReactNode }) {
  const [users, setUsers] = useState<UserRead[]>([]);
  const [userId, setUserIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .listUsers()
      .then((page) => {
        setUsers(page.items);
        const stored = localStorage.getItem(STORAGE_KEY);
        const initial =
          (stored && page.items.some((u) => u.id === stored) && stored) ||
          page.items[0]?.id ||
          null;
        setUserIdState(initial);
      })
      .finally(() => setLoading(false));
  }, []);

  function setUserId(id: string) {
    setUserIdState(id);
    localStorage.setItem(STORAGE_KEY, id);
  }

  return (
    <UserContext.Provider value={{ users, userId, setUserId, loading }}>
      {children}
    </UserContext.Provider>
  );
}

export function useUser() {
  const ctx = useContext(UserContext);
  if (!ctx) throw new Error("useUser must be used within UserProvider");
  return ctx;
}
