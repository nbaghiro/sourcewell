import * as React from "react";

import { api, API_URL, ApiError } from "@/lib/api";
import type { components } from "@/lib/api/schema";
import { clearApiTenant } from "@/lib/api/tenant";

// Derived from the backend's OpenAPI schema rather than re-declared here. The hand-written `Me`
// had drifted: it was missing `user.username` and `user.avatar_url`, both of which /auth/me has
// returned since the signup form started collecting them, so no caller could reach them.
type S = components["schemas"];

export type Workspace = S["WorkspaceSummary"];
export type Me = S["MeResponse"];
export type OrgSummary = S["OrgSummary"];
/** `oauth` is a single flag: Google and Microsoft are brokered by the same WorkOS app and are
 * configured together, so they are offered or withheld together. */
export type AuthOptions = S["AuthOptions"];
/** `email_sent` is false when the mail hop failed — the UI then surfaces a resend. */
export type SignupResult = S["AccountSignupResponse"];
/** `avatar` is a `data:image/...` URL the signup form produces by resizing the picked file;
 * omitted or null, the UI falls back to initials. */
export type SignupPayload = S["AccountSignupRequest"];
/** The profile fields both signup doors collect. An OAuth user posts these on their own, with no
 * email and no password — the provider established the address. */
export type SignupProfile = S["SignupProfile"];
/** The OAuth buttons we offer. LinkedIn is not one: it's connected in Settings as a sending
 * seat, by someone already signed in. */
export type OAuthProvider = "google" | "microsoft";

type Status = "loading" | "authed" | "anon";

interface AuthContextValue {
  status: Status;
  me: Me | null;
  /** Redirect to Google or Microsoft OAuth (brokered by WorkOS). */
  login: (provider: OAuthProvider) => void;
  /** Sign in with email + password. */
  passwordLogin: (creds: { email: string; password: string }) => Promise<void>;
  /** Self-serve signup: create the org + admin user. No session — the emailed link signs in. */
  signup: (payload: SignupPayload) => Promise<SignupResult>;
  /** Finish a signup that started at Google/Microsoft: supply the profile the provider couldn't. */
  completeProfile: (profile: SignupProfile) => Promise<void>;
  /** Re-send the confirmation link. Always resolves — the API never says who has an account. */
  resendVerification: (email: string) => Promise<void>;
  /** Whether this deployment has the OAuth buttons configured. Null while unknown. */
  options: AuthOptions | null;
  /** Mail a password-reset link. Always resolves, whether or not the address has an account. */
  forgotPassword: (email: string) => Promise<void>;
  /** Consume a reset link, set the new password, and sign in. */
  resetPassword: (token: string, password: string) => Promise<void>;
  /** Clear the session cookie and return to the login screen. */
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

/** A remembered workspace or organization the server no longer accepts (deleted, access revoked)
 * would otherwise wedge the whole app on 400/403, so drop the selection and ask again clean. */
async function fetchMe(): Promise<Me> {
  try {
    return await api<Me>("/auth/me");
  } catch (err) {
    if (err instanceof ApiError && (err.status === 400 || err.status === 403)) {
      clearApiTenant();
      return await api<Me>("/auth/me");
    }
    throw err;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = React.useState<Status>("loading");
  const [me, setMe] = React.useState<Me | null>(null);
  const [options, setOptions] = React.useState<AuthOptions | null>(null);

  // Whether the OAuth buttons are configured — the login screen only offers ones that work.
  React.useEffect(() => {
    void api<AuthOptions>("/auth/options")
      .then(setOptions)
      // A failure leaves this null rather than answering "nothing is configured". Those are
      // different facts: null means *we couldn't ask*, and the login screen's optimistic default
      // keeps the buttons up. Asserting false on any error meant a backend that was down or
      // briefly unreachable silently removed Google and Microsoft from the sign-in screen —
      // which reads as a configuration problem and sends you looking in the wrong place.
      .catch(() => {});
  }, []);

  const refresh = React.useCallback(async () => {
    try {
      setMe(await fetchMe());
      setStatus("authed");
      // The server gates the rest of the API on this too (403 profile_incomplete), so a client
      // that ignored it would just render pages full of failed requests. <RequireAuth> reads it.
    } catch {
      // 401, or a network error / backend down: either way there is no usable session, and the
      // app renders the login screen rather than a permanent splash loader.
      setMe(null);
      setStatus("anon");
    }
  }, []);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  // Both of these leave `status` alone until they succeed: flipping it to "loading" would swap
  // the form for the splash loader, unmounting it — and a failed attempt would come back to a
  // blank form with no error on it. The pages render their own in-button busy state instead.
  const passwordLogin = React.useCallback(
    async (creds: { email: string; password: string }) => {
      await api("/auth/password", { method: "POST", body: JSON.stringify(creds) });
      await refresh();
    },
    [refresh],
  );

  // No refresh() here: signup deliberately mints no session. The user is signed in when they
  // click the emailed link, which lands on the API and redirects back with the cookie set.
  const signup = React.useCallback(
    (payload: SignupPayload) =>
      api<SignupResult>("/auth/signup", { method: "POST", body: JSON.stringify(payload) }),
    [],
  );

  // Unlike signup this *does* refresh: the user is already signed in, and `profile_complete`
  // flipping is what lets <RequireAuth> stop routing them back to the form.
  const completeProfile = React.useCallback(
    async (profile: SignupProfile) => {
      await api("/auth/complete-profile", { method: "POST", body: JSON.stringify(profile) });
      await refresh();
    },
    [refresh],
  );

  const resendVerification = React.useCallback(async (email: string) => {
    await api("/auth/verify/resend", { method: "POST", body: JSON.stringify({ email }) });
  }, []);

  const forgotPassword = React.useCallback(async (email: string) => {
    await api("/auth/password/forgot", { method: "POST", body: JSON.stringify({ email }) });
  }, []);

  const resetPassword = React.useCallback(
    async (token: string, password: string) => {
      await api("/auth/password/reset", {
        method: "POST",
        body: JSON.stringify({ token, password }),
      });
      await refresh(); // the reset response carries the session — go straight into the app
    },
    [refresh],
  );

  const login = React.useCallback((provider: OAuthProvider) => {
    window.location.href = `${API_URL}/auth/login/${provider}`;
  }, []);

  const logout = React.useCallback(async () => {
    // A hard navigation, not a route change: it drops every cached query and React state along
    // with the session, so nothing from the old account can survive into the next sign-in.
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      window.location.href = "/login";
    }
  }, []);

  const value = React.useMemo(
    () => ({
      status,
      me,
      options,
      login,
      passwordLogin,
      signup,
      completeProfile,
      resendVerification,
      forgotPassword,
      resetPassword,
      logout,
      refresh,
    }),
    [
      status,
      me,
      options,
      login,
      passwordLogin,
      signup,
      completeProfile,
      resendVerification,
      forgotPassword,
      resetPassword,
      logout,
      refresh,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
