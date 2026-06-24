import * as React from "react";

import { api, API_URL, ApiError } from "@/lib/api";

export interface Workspace {
  id: string;
  name: string;
  kind: string;
}

export interface Me {
  user: { id: string; email: string; name: string } | null;
  organization: { id: string; name: string } | null;
  is_org_admin: boolean;
  current_workspace_id: string | null;
  workspaces: Workspace[];
}

type Status = "loading" | "authed" | "anon";

interface AuthContextValue {
  status: Status;
  me: Me | null;
  /** Redirect to WorkOS AuthKit to sign in. */
  login: () => void;
  /** Local-only: sign in as the demo admin, bypassing WorkOS (validates creds if given). */
  devLogin: (creds?: { email: string; password: string }) => Promise<void>;
  /** Clear the session and bounce through the WorkOS logout. */
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = React.useState<Status>("loading");
  const [me, setMe] = React.useState<Me | null>(null);

  const refresh = React.useCallback(async () => {
    try {
      const data = await api<Me>("/auth/me");
      setMe(data);
      setStatus("authed");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setMe(null);
        setStatus("anon");
      } else {
        // Network error / backend down — treat as signed out so the UI is usable.
        setMe(null);
        setStatus("anon");
      }
    }
  }, []);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  const devLogin = React.useCallback(
    async (creds?: { email: string; password: string }) => {
      setStatus("loading"); // swap the login page for the splash loader while we "sign in"
      try {
        await api("/auth/dev-login", {
          method: "POST",
          body: creds ? JSON.stringify(creds) : undefined,
        });
        await refresh();
      } catch (err) {
        setStatus("anon");
        throw err;
      }
    },
    [refresh],
  );

  const login = React.useCallback(() => {
    window.location.href = `${API_URL}/auth/login`;
  }, []);

  const logout = React.useCallback(async () => {
    try {
      const { logout_url } = await api<{ logout_url: string }>("/auth/logout", { method: "POST" });
      window.location.href = logout_url;
    } catch {
      window.location.href = "/login";
    }
  }, []);

  const value = React.useMemo(
    () => ({ status, me, login, devLogin, logout, refresh }),
    [status, me, login, devLogin, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
