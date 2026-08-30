import { CircleAlert, Loader2, LockKeyhole } from "lucide-react";
import * as React from "react";
import { Link, useSearchParams } from "react-router-dom";

import { AuthBrandPanel } from "@/components/auth-brand-panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const MIN_PASSWORD = 8;

/** Where the emailed reset link lands. A successful reset also signs the user in. */
export function ResetPasswordPage() {
  const { resetPassword } = useAuth();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";

  const [password, setPassword] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [errors, setErrors] = React.useState<{ password?: string; confirm?: string }>({});
  const [formError, setFormError] = React.useState<string | null>(null);
  const [dead, setDead] = React.useState(!token);
  const [busy, setBusy] = React.useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const found: { password?: string; confirm?: string } = {};
    if (password.length < MIN_PASSWORD) found.password = `Use at least ${MIN_PASSWORD} characters.`;
    if (confirm !== password) found.confirm = "Passwords don't match.";
    setErrors(found);
    setFormError(null);
    if (Object.keys(found).length > 0) return;

    setBusy(true);
    try {
      await resetPassword(token, password);
      // refresh() in the auth context flips status to authed — <ResetPasswordRoute> redirects in
    } catch (err) {
      setBusy(false);
      if (err instanceof ApiError && err.status === 400) setDead(true);
      else if (err instanceof ApiError && err.status === 422)
        setErrors({ password: `Use at least ${MIN_PASSWORD} characters.` });
      else setFormError("Couldn't reset your password. Try again in a moment.");
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      <AuthBrandPanel
        headline="Choose a new password."
        blurb="Once it's set we'll sign you straight in — and the link you used stops working."
      />

      <div className="grid place-items-center p-8">
        <div className="w-full max-w-sm">
          {dead ? (
            <>
              <span className="grid size-12 place-items-center rounded-xl bg-destructive/10 text-destructive">
                <CircleAlert className="size-6" />
              </span>
              <div className="mt-5 font-display text-2xl font-bold tracking-tight">
                That link has expired
              </div>
              <p className="mb-7 mt-2 text-sm leading-relaxed text-muted-foreground">
                Reset links work once and expire after an hour. Request a fresh one and it'll take
                you straight back here.
              </p>
              <Link to="/forgot-password">
                <Button size="lg" className="h-11 w-full justify-center">
                  Send a new link
                </Button>
              </Link>
            </>
          ) : (
            <form onSubmit={(e) => void submit(e)} noValidate>
              <span className="grid size-12 place-items-center rounded-xl bg-accent text-accent-foreground">
                <LockKeyhole className="size-6" />
              </span>
              <div className="mt-5 font-display text-2xl font-bold tracking-tight">
                Set a new password
              </div>
              <p className="mb-7 mt-2 text-sm leading-relaxed text-muted-foreground">
                Pick something you don't use anywhere else — at least {MIN_PASSWORD} characters.
              </p>

              <div className="grid gap-4">
                <div className="grid gap-1.5">
                  <Label htmlFor="password">New password</Label>
                  <Input
                    id="password"
                    type="password"
                    autoComplete="new-password"
                    placeholder="••••••••"
                    className={cn("h-11", errors.password && "border-destructive")}
                    value={password}
                    onChange={(e) => {
                      setPassword(e.target.value);
                      setErrors((x) => ({ ...x, password: undefined }));
                    }}
                  />
                  {errors.password && (
                    <p className="text-xs text-destructive">{errors.password}</p>
                  )}
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="confirm">Confirm new password</Label>
                  <Input
                    id="confirm"
                    type="password"
                    autoComplete="new-password"
                    placeholder="••••••••"
                    className={cn("h-11", errors.confirm && "border-destructive")}
                    value={confirm}
                    onChange={(e) => {
                      setConfirm(e.target.value);
                      setErrors((x) => ({ ...x, confirm: undefined }));
                    }}
                  />
                  {errors.confirm && <p className="text-xs text-destructive">{errors.confirm}</p>}
                </div>
              </div>

              {formError && <p className="mt-3 text-sm text-destructive">{formError}</p>}

              <Button
                type="submit"
                size="lg"
                className="mt-5 h-11 w-full justify-center"
                disabled={busy}
              >
                {busy && <Loader2 className="animate-spin" />}
                {busy ? "Saving…" : "Save and sign in"}
              </Button>
            </form>
          )}

          <p className="mt-6 text-center text-sm text-muted-foreground">
            <Link
              to="/login"
              className="font-medium text-foreground underline-offset-4 hover:underline"
            >
              Back to sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
