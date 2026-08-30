import { KeyRound, Loader2, MailCheck } from "lucide-react";
import * as React from "react";
import { Link, useSearchParams } from "react-router-dom";

import { AuthBrandPanel } from "@/components/auth-brand-panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function ForgotPasswordPage() {
  const { forgotPassword } = useAuth();
  const [params] = useSearchParams();
  const [email, setEmail] = React.useState(params.get("email") ?? "");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [sent, setSent] = React.useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!EMAIL_RE.test(email.trim())) {
      setError("Enter a valid email address.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await forgotPassword(email.trim().toLowerCase());
      setSent(true);
    } catch {
      setError("Couldn't send that just now. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      <AuthBrandPanel
        headline="Back into your workspace."
        blurb="We'll email you a link to choose a password — a new one, or your first if you've only signed in with Google or an invitation. It works once, and only for the person holding the inbox."
      />

      <div className="grid place-items-center p-8">
        <div className="w-full max-w-sm">
          {sent ? (
            <>
              <span className="grid size-12 place-items-center rounded-xl bg-accent text-accent-foreground">
                <MailCheck className="size-6" />
              </span>
              <div className="mt-5 font-display text-2xl font-bold tracking-tight">
                Check your inbox
              </div>
              <p className="mb-7 mt-2 text-sm leading-relaxed text-muted-foreground">
                If <span className="font-medium text-foreground">{email.trim().toLowerCase()}</span>{" "}
                has a confirmed Sourcewell account, a link is on its way. It expires in an hour.
              </p>
              <Button
                variant="outline"
                size="lg"
                className="h-11 w-full justify-center"
                onClick={() => setSent(false)}
              >
                Use a different address
              </Button>
            </>
          ) : (
            <form onSubmit={(e) => void submit(e)} noValidate>
              <span className="grid size-12 place-items-center rounded-xl bg-accent text-accent-foreground">
                <KeyRound className="size-6" />
              </span>
              <div className="mt-5 font-display text-2xl font-bold tracking-tight">
                Reset your password
              </div>
              <p className="mb-7 mt-2 text-sm leading-relaxed text-muted-foreground">
                Enter the address you signed up with and we'll send you a link to choose a new
                password.
              </p>

              <div className="grid gap-1.5">
                <Label htmlFor="email">Work email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  placeholder="you@company.com"
                  className={cn("h-11", error && "border-destructive")}
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    setError(null);
                  }}
                />
              </div>
              {error && <p className="mt-2 text-sm text-destructive">{error}</p>}

              <Button
                type="submit"
                size="lg"
                className="mt-4 h-11 w-full justify-center"
                disabled={busy}
              >
                {busy && <Loader2 className="animate-spin" />}
                {busy ? "Sending…" : "Send the reset link"}
              </Button>
            </form>
          )}

          <p className="mt-6 text-center text-sm text-muted-foreground">
            Remembered it?{" "}
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
