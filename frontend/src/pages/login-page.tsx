import { Check, ShieldCheck } from "lucide-react";
import * as React from "react";

import { BrandMark } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth";

const FEATURES = [
  "SSO & SCIM for your whole org",
  "Per-workspace access for each client team",
  "Human-in-the-loop on every send",
];

function GoogleIcon() {
  return (
    <svg viewBox="0 0 48 48" className="size-[18px]" aria-hidden>
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

function MicrosoftIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-[15px]" aria-hidden>
      <rect x="1" y="1" width="10" height="10" fill="#F25022" />
      <rect x="13" y="1" width="10" height="10" fill="#7FBA00" />
      <rect x="1" y="13" width="10" height="10" fill="#00A4EF" />
      <rect x="13" y="13" width="10" height="10" fill="#FFB900" />
    </svg>
  );
}

function LinkedInIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-[16px]" aria-hidden>
      <path
        fill="#0A66C2"
        d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.13 1.45-2.13 2.94v5.67H9.35V9h3.41v1.56h.05c.48-.9 1.63-1.85 3.36-1.85 3.6 0 4.27 2.37 4.27 5.45v6.29zM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13zM7.12 20.45H3.56V9h3.56v11.45zM22.22 0H1.77C.79 0 0 .77 0 1.73v20.54C0 23.22.79 24 1.77 24h20.45c.98 0 1.78-.78 1.78-1.73V1.73C24 .77 23.2 0 22.22 0z"
      />
    </svg>
  );
}

/** Abstract "talent network" line illustration — translucent, no solid background. */
function HeroArt() {
  const nodes: [number, number, number][] = [
    [120, 90, 5], [210, 140, 6], [300, 80, 5], [450, 90, 5], [160, 220, 6],
    [350, 250, 6], [440, 210, 6], [110, 320, 5], [220, 330, 7], [320, 340, 6],
    [420, 330, 6], [180, 420, 6], [400, 430, 5],
  ];
  const lines: [number, number, number, number][] = [
    [120, 90, 210, 140], [210, 140, 300, 80], [300, 80, 380, 150], [380, 150, 450, 90],
    [210, 140, 160, 220], [160, 220, 260, 210], [260, 210, 380, 150], [260, 210, 350, 250],
    [350, 250, 440, 210], [380, 150, 440, 210], [160, 220, 110, 320], [260, 210, 220, 330],
    [220, 330, 320, 340], [320, 340, 350, 250], [320, 340, 420, 330], [420, 330, 440, 210],
    [110, 320, 220, 330], [220, 330, 180, 420], [320, 340, 400, 430], [400, 430, 420, 330],
  ];
  const matched: [number, number][] = [[380, 150], [260, 210], [320, 340]];

  return (
    <svg
      viewBox="0 0 520 520"
      preserveAspectRatio="xMidYMid slice"
      className="pointer-events-none absolute inset-0 h-full w-full opacity-90"
      aria-hidden
    >
      {[130, 200, 270].map((r) => (
        <circle key={r} cx="320" cy="220" r={r} fill="none" stroke="white" strokeOpacity="0.05" />
      ))}
      {lines.map(([x1, y1, x2, y2], i) => (
        <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="white" strokeOpacity="0.13" />
      ))}
      {nodes.map(([cx, cy, r], i) => (
        <circle key={i} cx={cx} cy={cy} r={r} fill="white" fillOpacity="0.06" stroke="white" strokeOpacity="0.28" />
      ))}
      {matched.map(([cx, cy], i) => (
        <g key={i}>
          <circle cx={cx} cy={cy} r="14" fill="none" stroke="#43B68F" strokeOpacity="0.4" strokeWidth="1.5" />
          <circle cx={cx} cy={cy} r="9" fill="#43B68F" />
          <path
            d={`M${cx - 4} ${cy} l3 3 l5 -6`}
            stroke="#06241b"
            strokeWidth="2"
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </g>
      ))}
    </svg>
  );
}

export function LoginPage() {
  const { login, linkedinLogin, devLogin } = useAuth();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  async function continueWithEmail() {
    if (email && password) {
      setBusy(true);
      setError(null);
      try {
        await devLogin({ email, password });
      } catch {
        setError("Invalid email or password.");
        setBusy(false);
      }
    } else {
      login();
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      {/* brand panel */}
      <div
        className="relative hidden flex-col overflow-hidden p-14 text-[#EBF7F1] lg:flex"
        style={{
          background:
            "linear-gradient(150deg, var(--sidebar), var(--sidebar-active) 58%, var(--score-from))",
        }}
      >
        <HeroArt />
        <div className="relative z-10 flex items-center gap-2.5 font-display text-2xl font-bold">
          <span className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-score-from to-score-to text-primary-foreground">
            <BrandMark className="size-5" />
          </span>
          Sourcewell
        </div>
        <h1 className="relative z-10 mt-auto max-w-[11ch] font-display text-[2.6rem] font-bold leading-[1.08] tracking-tight">
          Source the people you can't find.
        </h1>
        <p className="relative z-10 mt-5 max-w-[42ch] text-[15px] leading-relaxed text-[#A9D4C8]">
          AI agents that find, rank, and reach candidates across email and LinkedIn — every message
          waits for your approval.
        </p>
        <div className="relative z-10 mt-8 flex flex-col gap-3">
          {FEATURES.map((f) => (
            <div key={f} className="flex items-center gap-3 text-sm text-[#CDE8DF]">
              <span className="grid size-5 place-items-center rounded-md bg-[#0E7C66]">
                <Check className="size-3" strokeWidth={3} />
              </span>
              {f}
            </div>
          ))}
        </div>
      </div>

      {/* auth panel */}
      <div className="grid place-items-center p-8">
        <div className="w-full max-w-sm">
          <div className="font-display text-2xl font-bold tracking-tight">Sign in to Sourcewell</div>
          <p className="mb-7 mt-2 text-sm text-muted-foreground">Use your company account to continue.</p>

          <div className="flex flex-col gap-2.5">
            <Button size="lg" className="h-11 w-full justify-center" onClick={login}>
              <ShieldCheck /> Continue with SSO
            </Button>
            <Button variant="outline" size="lg" className="h-11 w-full justify-center" onClick={login}>
              <GoogleIcon /> Continue with Google
            </Button>
            <Button variant="outline" size="lg" className="h-11 w-full justify-center" onClick={login}>
              <MicrosoftIcon /> Continue with Microsoft
            </Button>
            <Button
              variant="outline"
              size="lg"
              className="h-11 w-full justify-center"
              onClick={linkedinLogin}
            >
              <LinkedInIcon /> Continue with LinkedIn
            </Button>
          </div>

          <div className="my-5 flex items-center gap-3 text-xs text-muted-foreground">
            <span className="h-px flex-1 bg-border" /> or <span className="h-px flex-1 bg-border" />
          </div>

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
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="password">Password</Label>
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

          <p className="mt-6 flex items-center gap-2 text-xs text-muted-foreground">
            <span className="rounded-md border border-border bg-secondary px-1.5 py-0.5 font-mono text-[11px] text-foreground">
              Secured by WorkOS
            </span>
            SSO, MFA &amp; audit logs
          </p>

          <p className="mt-3 text-xs text-muted-foreground">
            Demo:{" "}
            <button
              type="button"
              className="font-mono text-foreground underline-offset-2 hover:underline"
              onClick={() => void devLogin()}
            >
              demo@sourcewell.ai · pass
            </button>
          </p>
        </div>
      </div>
    </div>
  );
}
