import { CircleAlert } from "lucide-react";
import * as React from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { AuthBrandPanel } from "@/components/auth-brand-panel";
import { GoogleIcon, MicrosoftIcon } from "@/components/brand-icons";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const REDIRECT_ERRORS: Record<string, string> = {
  auth_failed: "That sign-in didn't complete. Try again, or use your email and password.",
  provider_unavailable:
    "That sign-in method isn't set up for this workspace yet. Sign in with your email instead.",
  account_disabled: "This account has been disabled. Ask an admin in your org to re-enable it.",
  invite_invalid:
    "That invitation link has expired. Ask whoever invited you to send a fresh one.",
};

export function LoginPage() {
  const { login, passwordLogin, options } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  // A provider round-trip that failed comes back here with ?error= — otherwise the user lands on
  // a blank login form with no idea what went wrong.
  const redirectError = REDIRECT_ERRORS[params.get("error") ?? ""];

  // Until /auth/options answers, assume the OAuth buttons are there — it's the common deployment,
  // and a brief flash of a button that then disappears is worse than one that appears.
  const oauth = options?.oauth ?? true;

  async function continueWithEmail() {
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await passwordLogin({ email, password });
    } catch (err) {
      setBusy(false);
      if (!(err instanceof ApiError)) {
        setError("Couldn't sign you in. Try again in a moment.");
        return;
      }
      const detail = err.message;
      // Right password, unconfirmed address — send them to the resend screen rather than
      // leaving them staring at an error they can't act on.
      if (err.status === 403 && detail.includes("email_not_verified")) {
        navigate(`/verify-email?email=${encodeURIComponent(email.trim().toLowerCase())}`);
        return;
      }
      if (err.status === 403) {
        setError("This account has been disabled. Ask an admin in your org to re-enable it.");
      } else if (err.status === 429) {
        setError("Too many attempts. Try again in a few minutes, or reset your password.");
      } else {
        setError("Invalid email or password.");
      }
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      <AuthBrandPanel
        headline="Source the people you can't find."
        blurb="AI agents that find, rank, and reach candidates across email and LinkedIn — every message waits for your approval."
      />

      {/* auth panel */}
      <div className="grid place-items-center p-8">
        <div className="w-full max-w-sm">
          <div className="font-display text-2xl font-bold tracking-tight">Sign in to Sourcewell</div>
          <p className="mb-7 mt-2 text-sm text-muted-foreground">Use your company account to continue.</p>

          {redirectError && (
            <p className="mb-5 flex items-start gap-2 rounded-lg border border-border bg-secondary px-3 py-2.5 text-sm text-foreground">
              <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
              {redirectError}
            </p>
          )}

          {oauth && (
            <>
              <div className="flex flex-col gap-2.5">
                <Button
                  variant="outline"
                  size="lg"
                  className="h-11 w-full justify-center"
                  onClick={() => login("google")}
                >
                  <GoogleIcon /> Continue with Google
                </Button>
                <Button
                  variant="outline"
                  size="lg"
                  className="h-11 w-full justify-center"
                  onClick={() => login("microsoft")}
                >
                  <MicrosoftIcon /> Continue with Microsoft
                </Button>
              </div>

              <div className="my-5 flex items-center gap-3 text-xs text-muted-foreground">
                <span className="h-px flex-1 bg-border" /> or{" "}
                <span className="h-px flex-1 bg-border" />
              </div>
            </>
          )}

          <div className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="email">Work email</Label>
              <Input
                id="email"
                type="email"
                placeholder="you@company.com"
                className="h-11"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void continueWithEmail()}
              />
            </div>
            <div className="grid gap-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="password">Password</Label>
                <Link
                  to={`/forgot-password${email.trim() ? `?email=${encodeURIComponent(email.trim())}` : ""}`}
                  className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                >
                  Forgot password?
                </Link>
              </div>
              <Input
                id="password"
                type="password"
                placeholder="••••••••"
                className="h-11"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void continueWithEmail()}
              />
            </div>
          </div>

          {error && <p className="mt-2 text-sm text-destructive">{error}</p>}

          <Button size="lg" className="mt-3 h-11 w-full justify-center" disabled={busy} onClick={() => void continueWithEmail()}>
            {busy ? "Signing in…" : "Continue with email"}
          </Button>

          <p className="mt-5 text-center text-sm text-muted-foreground">
            New to Sourcewell?{" "}
            <Link to="/signup" className="font-medium text-foreground underline-offset-4 hover:underline">
              Create an account
            </Link>
          </p>

        </div>
      </div>
    </div>
  );
}
