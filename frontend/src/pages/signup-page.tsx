import { ImagePlus, Loader2, Trash2 } from "lucide-react";
import * as React from "react";
import { Link, useNavigate } from "react-router-dom";

import { AuthBrandPanel } from "@/components/auth-brand-panel";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import { useAuth, type SignupPayload, type SignupProfile } from "@/lib/auth";
import { cn } from "@/lib/utils";

/** Mirrors the server-side rules in `backend/app/api/auth.py` / `services/workspace/auth.py`. */
const USERNAME_RE = /^[a-z0-9][a-z0-9._-]{2,29}$/;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const MIN_PASSWORD = 8;
const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
const AVATAR_PX = 256;

type Field =
  | "first_name"
  | "last_name"
  | "username"
  | "email"
  | "company_name"
  | "avatar"
  | "password"
  | "confirm";

type Form = Record<Field, string>;
type Errors = Partial<Record<Field, string>>;

const EMPTY: Form = {
  first_name: "",
  last_name: "",
  username: "",
  email: "",
  company_name: "",
  avatar: "",
  password: "",
  confirm: "",
};

/** `oauth` = finishing a Google/Microsoft signup: the address came from the provider and there is
 * no password to choose, so neither is validated here. */
function validate(form: Form, oauth: boolean): Errors {
  const e: Errors = {};
  if (!form.first_name.trim()) e.first_name = "First name is required.";
  if (!form.last_name.trim()) e.last_name = "Last name is required.";
  if (!form.username.trim()) e.username = "Username is required.";
  else if (!USERNAME_RE.test(form.username.trim().toLowerCase()))
    e.username = "3–30 characters: letters, numbers, dot, dash or underscore.";
  if (!form.company_name.trim()) e.company_name = "Company name is required.";
  if (oauth) return e;
  if (!form.email.trim()) e.email = "Work email is required.";
  else if (!EMAIL_RE.test(form.email.trim())) e.email = "Enter a valid email address.";
  if (form.password.length < MIN_PASSWORD)
    e.password = `Use at least ${MIN_PASSWORD} characters.`;
  if (form.confirm !== form.password) e.confirm = "Passwords don't match.";
  return e;
}

/** Draw the picked file into a square canvas (center-cropped) so uploads stay ~30 KB. */
async function toAvatarDataUrl(file: File): Promise<string> {
  const source = await new Promise<HTMLImageElement>((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("unreadable image"));
    };
    img.src = url;
  });
  const side = Math.min(source.width, source.height);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = AVATAR_PX;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("no canvas");
  ctx.drawImage(
    source,
    (source.width - side) / 2,
    (source.height - side) / 2,
    side,
    side,
    0,
    0,
    AVATAR_PX,
    AVATAR_PX,
  );
  return canvas.toDataURL("image/jpeg", 0.85);
}

function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return <p className="text-xs text-destructive">{message}</p>;
}

export function SignupPage() {
  const { signup, completeProfile, me, status } = useAuth();
  const navigate = useNavigate();
  // Two doors into the same form. Anonymous = a fresh email+password signup. Signed in with an
  // unfinished profile = the tail of a Google/Microsoft signup: the account already exists and
  // its address is already verified, so the email is theirs to see and not to change, and there
  // is no password to choose.
  const oauth = status === "authed" && me?.profile_complete === false;
  const providerEmail = me?.user?.email ?? "";
  const [form, setForm] = React.useState<Form>(EMPTY);
  const [errors, setErrors] = React.useState<Errors>({});
  const [formError, setFormError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const fileInput = React.useRef<HTMLInputElement>(null);

  const set = (field: Field) => (value: string) => {
    setForm((f) => ({ ...f, [field]: value }));
    setErrors((e) => ({ ...e, [field]: undefined }));
  };

  // Prefill from the provider once /auth/me lands. The display name is a best-effort split — a
  // starting point the user can correct, not a value we insist on.
  React.useEffect(() => {
    if (!oauth) return;
    const [first = "", ...rest] = (me?.user?.name ?? "").trim().split(/\s+/);
    setForm((f) => ({
      ...f,
      email: providerEmail,
      first_name: f.first_name || first,
      last_name: f.last_name || rest.join(" "),
      avatar: f.avatar || me?.user?.avatar_url || "",
    }));
  }, [oauth, providerEmail, me?.user?.name, me?.user?.avatar_url]);

  const initials =
    `${form.first_name.trim()[0] ?? ""}${form.last_name.trim()[0] ?? ""}`.toUpperCase();

  async function pickAvatar(file: File | undefined) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setErrors((e) => ({ ...e, avatar: "Pick an image file (JPG, PNG or WebP)." }));
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setErrors((e) => ({ ...e, avatar: "That file is over 5 MB — pick a smaller one." }));
      return;
    }
    try {
      set("avatar")(await toAvatarDataUrl(file));
    } catch {
      setErrors((e) => ({ ...e, avatar: "That image couldn't be read." }));
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const found = validate(form, oauth);
    setErrors(found);
    setFormError(null);
    if (Object.keys(found).length > 0) return;

    setBusy(true);
    const profile: SignupProfile = {
      first_name: form.first_name.trim(),
      last_name: form.last_name.trim(),
      username: form.username.trim().toLowerCase(),
      company_name: form.company_name.trim(),
      avatar: form.avatar || null,
    };
    try {
      if (oauth) {
        // Already signed in — finishing this drops them into the app, so there is nothing to
        // navigate to: `profile_complete` flipping is what releases <RequireAuth>.
        await completeProfile(profile);
        return;
      }
      const payload: SignupPayload = {
        ...profile,
        email: form.email.trim().toLowerCase(),
        password: form.password,
      };
      const result = await signup(payload);
      // No session yet: the account is inert until the emailed link is clicked.
      navigate(
        `/verify-email?email=${encodeURIComponent(result.email)}` +
          (result.email_sent ? "" : "&sent=0"),
      );
    } catch (err) {
      setBusy(false);
      if (err instanceof ApiError && err.status === 409) {
        const taken = err.message.toLowerCase();
        if (taken.includes("username")) setErrors({ username: "That username is taken." });
        else if (oauth) setFormError("Couldn't finish setting up your account. Try again.");
        else setErrors({ email: "That email is already registered." });
      } else if (err instanceof ApiError && err.status === 422) {
        setFormError("Some details weren't accepted — check the fields above.");
      } else {
        setFormError(
          oauth
            ? "Couldn't finish setting up your account. Try again in a moment."
            : "Couldn't create your account. Try again in a moment.",
        );
      }
    }
  }

  const invalid = (field: Field) =>
    cn(errors[field] && "border-destructive focus-visible:border-destructive");

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      <AuthBrandPanel
        headline={oauth ? "Nearly there." : "Start sourcing in minutes."}
        blurb={
          oauth
            ? "Your account is confirmed. A few details about you and your company and your workspace is ready."
            : "Create your workspace, connect your inbox, and let the agents find, rank, and reach the people you're looking for."
        }
      />

      {/* signup panel */}
      <div className="grid place-items-center overflow-y-auto p-8">
        <form className="w-full max-w-md py-4" onSubmit={(e) => void submit(e)} noValidate>
          <div className="font-display text-2xl font-bold tracking-tight">
            {oauth ? "Finish setting up" : "Create your account"}
          </div>
          <p className="mb-7 mt-2 text-sm text-muted-foreground">
            {oauth
              ? "We got your email from your provider — we just need a few details it doesn't carry."
              : "This is your org's admin account. Everything except the photo is required."}
          </p>

          {/* avatar */}
          <div className="grid gap-1.5">
            <Label>
              Profile photo{" "}
              <span className="font-normal text-muted-foreground">(optional)</span>
            </Label>
            <div className="flex items-center gap-4">
              <Avatar className="size-16 rounded-xl border border-border">
                {form.avatar && <AvatarImage src={form.avatar} alt="" />}
                <AvatarFallback className="rounded-xl text-base">
                  {initials || "?"}
                </AvatarFallback>
              </Avatar>
              <div className="flex flex-col items-start gap-1.5">
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => fileInput.current?.click()}
                  >
                    <ImagePlus /> {form.avatar ? "Change photo" : "Upload photo"}
                  </Button>
                  {form.avatar && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => set("avatar")("")}
                      aria-label="Remove photo"
                    >
                      <Trash2 />
                    </Button>
                  )}
                </div>
                <span className="text-xs text-muted-foreground">
                  JPG, PNG or WebP · up to 5 MB. We'll use your initials without one.
                </span>
              </div>
              <input
                ref={fileInput}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  void pickAvatar(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
            </div>
            <FieldError message={errors.avatar} />
          </div>

          <div className="mt-4 grid gap-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="first_name">First name</Label>
                <Input
                  id="first_name"
                  className={cn("h-11", invalid("first_name"))}
                  autoComplete="given-name"
                  placeholder="Ada"
                  value={form.first_name}
                  onChange={(e) => set("first_name")(e.target.value)}
                />
                <FieldError message={errors.first_name} />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="last_name">Last name</Label>
                <Input
                  id="last_name"
                  className={cn("h-11", invalid("last_name"))}
                  autoComplete="family-name"
                  placeholder="Lovelace"
                  value={form.last_name}
                  onChange={(e) => set("last_name")(e.target.value)}
                />
                <FieldError message={errors.last_name} />
              </div>
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="username">Username</Label>
              <div className="relative">
                <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground">
                  @
                </span>
                <Input
                  id="username"
                  className={cn("h-11 pl-7", invalid("username"))}
                  autoComplete="username"
                  placeholder="ada"
                  value={form.username}
                  onChange={(e) => set("username")(e.target.value.toLowerCase())}
                />
              </div>
              <FieldError message={errors.username} />
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="email">Work email</Label>
              <Input
                id="email"
                type="email"
                className={cn("h-11", invalid("email"), oauth && "bg-secondary text-muted-foreground")}
                autoComplete="email"
                placeholder="you@company.com"
                value={form.email}
                onChange={(e) => set("email")(e.target.value)}
                // The provider owns this address and already verified it. Read-only rather than
                // hidden, so the user can see which account they're finishing.
                readOnly={oauth}
                disabled={oauth}
              />
              {oauth ? (
                <p className="text-xs text-muted-foreground">
                  Confirmed by your provider — sign in with that account to get back in.
                </p>
              ) : (
                <FieldError message={errors.email} />
              )}
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="company_name">Company name</Label>
              <Input
                id="company_name"
                className={cn("h-11", invalid("company_name"))}
                autoComplete="organization"
                placeholder="Acme Talent"
                value={form.company_name}
                onChange={(e) => set("company_name")(e.target.value)}
              />
              <FieldError message={errors.company_name} />
            </div>

            {!oauth && (
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  className={cn("h-11", invalid("password"))}
                  autoComplete="new-password"
                  placeholder="••••••••"
                  value={form.password}
                  onChange={(e) => set("password")(e.target.value)}
                />
                <FieldError message={errors.password} />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="confirm">Confirm password</Label>
                <Input
                  id="confirm"
                  type="password"
                  className={cn("h-11", invalid("confirm"))}
                  autoComplete="new-password"
                  placeholder="••••••••"
                  value={form.confirm}
                  onChange={(e) => set("confirm")(e.target.value)}
                />
                <FieldError message={errors.confirm} />
              </div>
            </div>
            )}
          </div>

          {formError && <p className="mt-3 text-sm text-destructive">{formError}</p>}

          <Button type="submit" size="lg" className="mt-5 h-11 w-full justify-center" disabled={busy}>
            {busy && <Loader2 className="animate-spin" />}
            {busy
              ? oauth
                ? "Setting up your workspace…"
                : "Creating your account…"
              : oauth
                ? "Finish and continue"
                : "Create account"}
          </Button>

          {!oauth && (
            <p className="mt-5 text-center text-sm text-muted-foreground">
              Already have an account?{" "}
              <Link to="/login" className="font-medium text-foreground underline-offset-4 hover:underline">
                Sign in
              </Link>
            </p>
          )}
        </form>
      </div>
    </div>
  );
}
