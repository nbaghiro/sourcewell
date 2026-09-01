import { CircleAlert, Loader2, MailCheck } from "lucide-react";
import * as React from "react";
import { Link, useSearchParams } from "react-router-dom";

import { AuthBrandPanel } from "@/components/auth-brand-panel";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

const RESEND_COOLDOWN_S = 60;

/** Where signup lands: the account exists but stays inert until the emailed link is clicked. */
export function VerifyEmailPage() {
  const { resendVerification } = useAuth();
  const [params] = useSearchParams();
  const email = params.get("email") ?? "";
  const linkInvalid = params.get("error") === "link_invalid";
  const sendFailed = params.get("sent") === "0";

  const [cooldown, setCooldown] = React.useState(0);
  const [busy, setBusy] = React.useState(false);
  const [note, setNote] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  async function resend() {
    if (!email || busy || cooldown > 0) return;
    setBusy(true);
    setNote(null);
    try {
      await resendVerification(email);
      setNote(`Sent again to ${email}.`);
      setCooldown(RESEND_COOLDOWN_S);
    } catch {
      setNote("Couldn't send just now — try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      <AuthBrandPanel
        headline="Check your inbox."
        blurb="Confirming your address keeps Sourcewell's sending domain trusted — which is what gets your outreach into candidates' inboxes rather than their spam folders."
      />

      <div className="grid place-items-center p-8">
        <div className="w-full max-w-sm">
          <span
            className={
              linkInvalid
                ? "grid size-12 place-items-center rounded-xl bg-destructive/10 text-destructive"
                : "grid size-12 place-items-center rounded-xl bg-accent text-accent-foreground"
            }
          >
            {linkInvalid ? <CircleAlert className="size-6" /> : <MailCheck className="size-6" />}
          </span>

          <div className="mt-5 font-display text-2xl font-bold tracking-tight">
            {linkInvalid ? "That link has expired" : "Confirm your email"}
          </div>

          {linkInvalid ? (
            <p className="mb-7 mt-2 text-sm leading-relaxed text-muted-foreground">
              Confirmation links expire after 24 hours. Send yourself a fresh one and it'll sign
              you straight in.
            </p>
          ) : (
            <p className="mb-7 mt-2 text-sm leading-relaxed text-muted-foreground">
              We sent a confirmation link to{" "}
              <span className="font-medium text-foreground">{email || "your email address"}</span>.
              Click it and you'll be signed in — your account stays inactive until then.
            </p>
          )}

          {sendFailed && (
            <p className="mb-4 rounded-lg border border-border bg-secondary px-3 py-2 text-sm text-foreground">
              We couldn't send that email just now. Try again below.
            </p>
          )}

          <Button
            size="lg"
            className="h-11 w-full justify-center"
            disabled={!email || busy || cooldown > 0}
            onClick={() => void resend()}
          >
            {busy && <Loader2 className="animate-spin" />}
            {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend the link"}
          </Button>

          {note && <p className="mt-2 text-sm text-muted-foreground">{note}</p>}

          <p className="mt-6 text-sm text-muted-foreground">
            Wrong address?{" "}
            <Link
              to="/signup"
              className="font-medium text-foreground underline-offset-4 hover:underline"
            >
              Start over
            </Link>{" "}
            ·{" "}
            <Link
              to="/login"
              className="font-medium text-foreground underline-offset-4 hover:underline"
            >
              Back to sign in
            </Link>
          </p>

          <p className="mt-6 text-xs leading-relaxed text-muted-foreground">
            Nothing in your inbox after a minute? Check spam, and make sure
            {email ? ` ${email} ` : " the address "}
            is spelled correctly.
          </p>
        </div>
      </div>
    </div>
  );
}
