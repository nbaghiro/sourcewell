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

/** Which sign-in methods the backend has configured — drives which buttons the login page shows. */
export interface AuthOptions {
  workos: boolean;
  linkedin: boolean;
  password: boolean;
}

type Status = "loading" | "authed" | "anon";

interface AuthContextValue {
  status: Status;
  me: Me | null;
  /** Configured sign-in methods (null while still loading). */
  options: AuthOptions | null;
  /** Redirect to WorkOS AuthKit. Pass an `idp` to deep-link straight to Google / Microsoft. */
  login: (idp?: "google" | "microsoft") => void;
  /** Redirect to the LinkedIn hosted-auth (Unipile) sign-in. */
  linkedinLogin: () => void;
  /** Sign in with email + password (the seeded demo account: demo@sourcewell.ai). */
  passwordLogin: (creds: { email: string; password: string }) => Promise<void>;
  /** Clear the session and bounce through the WorkOS logout. */
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = React.useState<Status>("loading");
  const [me, setMe] = React.useState<Me | null>(null);
  const [options, setOptions] = React.useState<AuthOptions | null>(null);

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
    // Sign-in methods are public; fall back to password-only if the probe fails so the form still renders.
    void api<AuthOptions>("/auth/options")
      .then(setOptions)
      .catch(() => setOptions({ workos: false, linkedin: false, password: true }));
  }, [refresh]);

  const passwordLogin = React.useCallback(
    async (creds: { email: string; password: string }) => {
      setStatus("loading"); // swap the login page for the splash loader while we sign in
      try {
        await api("/auth/password", { method: "POST", body: JSON.stringify(creds) });
        await refresh();
      } catch (err) {
        setStatus("anon");
        throw err;
      }
    },
    [refresh],
  );

  const login = React.useCallback((idp?: "google" | "microsoft") => {
    window.location.href = `${API_URL}/auth/login${idp ? `?provider=${idp}` : ""}`;
  }, []);

  const linkedinLogin = React.useCallback(() => {
    window.location.href = `${API_URL}/auth/linkedin/login`;
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
    () => ({ status, me, options, login, linkedinLogin, passwordLogin, logout, refresh }),
    [status, me, options, login, linkedinLogin, passwordLogin, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
