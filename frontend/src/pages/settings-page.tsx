import { Database, Mail, Plus, ShieldCheck, Trash2 } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { LinkedInIcon, MicrosoftIcon } from "@/components/brand-icons";
import { PageHeader } from "@/components/page-header";
import { PageLayout } from "@/components/page-layout";
import { ReportingTab } from "@/pages/analytics-page";
import { AuditTab } from "@/pages/audit-page";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  type DataProvider,
  useAccountUsage,
  useAddSuppression,
  useConnect,
  useConnections,
  useDataProviders,
  useDeleteDataProvider,
  useDisconnect,
  useInviteMember,
  useMembers,
  useReauth,
  useRemoveMember,
  useRemoveSuppression,
  useSaveDataProvider,
  useSuppressions,
  useUpdateMemberRole,
  useUpdateWorkspaceSettings,
  useVerifyDataProvider,
  useWorkspaceSettings,
} from "@/lib/api/queries";
import { initials } from "@/lib/format";

const DATA_PROVIDER_META: Record<string, string> = {
  pdl: "Search + enrich · the platform engine",
  apollo: "Search + enrich + verify",
  cognism: "EU mobile-verified · GDPR-first",
  hunter: "Email find + verify",
};

const PROVIDER: Record<string, { label: string; icon: React.ReactNode; color?: string }> = {
  gmail: { label: "Email · Gmail", icon: <Mail className="size-5" />, color: "#C5402F" },
  graph: { label: "Email · Microsoft", icon: <MicrosoftIcon className="size-4" /> },
  linkedin: { label: "LinkedIn", icon: <LinkedInIcon className="size-5" />, color: "#0A66C2" },
};
const CONN_STATUS: Record<string, { label: string; variant: BadgeProps["variant"] }> = {
  ok: { label: "Connected", variant: "success" },
  needs_reauth: { label: "Reconnect needed", variant: "warning" },
  paused: { label: "Paused", variant: "secondary" },
};
const ROLE: Record<string, { label: string; variant: BadgeProps["variant"] }> = {
  org_admin: { label: "Org admin", variant: "accent" },
  workspace_admin: { label: "Workspace admin", variant: "secondary" },
  member: { label: "Member", variant: "outline" },
  compliance: { label: "Compliance", variant: "warning" },
};

export function SettingsPage() {
  const [tab, setTab] = React.useState("connections");
  return (
    <PageLayout width={tab === "reporting" ? "wide" : "narrow"}>
      <PageHeader eyebrow="Workspace" title="Settings" description="Channels, your team, reporting, and how autonomously the agent operates." />
      <Segmented
        value={tab}
        onChange={setTab}
        options={[
          { value: "plan", label: "Plan & usage" },
          { value: "connections", label: "Connections" },
          { value: "providers", label: "Data providers" },
          { value: "suppression", label: "Suppression" },
          { value: "members", label: "Members" },
          { value: "autonomy", label: "Autonomy" },
          { value: "reporting", label: "Reporting" },
          { value: "audit", label: "Audit" },
        ]}
      />
      {tab === "plan" && <PlanUsageTab />}
      {tab === "connections" && <ConnectionsTab />}
      {tab === "providers" && <ProvidersTab />}
      {tab === "suppression" && <SuppressionTab />}
      {tab === "members" && <MembersTab />}
      {tab === "autonomy" && <AutonomyTab />}
      {tab === "reporting" && <ReportingTab />}
      {tab === "audit" && <AuditTab />}
    </PageLayout>
  );
}

function PlanUsageTab() {
  const { data } = useAccountUsage();
  if (!data)
    return (
      <Card>
        <CardContent className="py-6">
          <Skeleton className="h-44" />
        </CardContent>
      </Card>
    );
  const pct = Math.min(100, data.pct);
  const tone = data.over
    ? "var(--destructive)"
    : data.pct >= 80
      ? "var(--warning)"
      : "var(--primary)";
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Plan &amp; usage</CardTitle>
          <Badge variant="accent" className="capitalize">
            {data.plan}
          </Badge>
        </div>
        <span className="text-xs text-muted-foreground">
          One pooled monthly credit balance — resets at the start of each month.
        </span>
      </CardHeader>
      <CardContent className="space-y-5">
        <div>
          <div className="flex items-baseline justify-between">
            <span className="font-display text-2xl font-semibold text-foreground">
              {data.used.toLocaleString()}
            </span>
            <span className="text-sm text-muted-foreground">
              of {data.allowance.toLocaleString()} credits · {data.pct}%
            </span>
          </div>
          <div className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-secondary">
            <div
              className="h-full rounded-full transition-all"
              style={{ width: `${pct}%`, background: tone }}
            />
          </div>
        </div>

        {data.over ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3.5 py-2.5 text-sm text-destructive">
            You're {data.pct - 100}% over this month's credits. Sending and sourcing keep working —
            overage is reconciled at billing. Upgrade for more headroom.
          </div>
        ) : data.pct >= 80 ? (
          <p className="text-sm" style={{ color: "var(--warning)" }}>
            You've used {data.pct}% of this month's credits.
          </p>
        ) : null}

        <div className="grid grid-cols-3 gap-3">
          <UsageStat label="Emails sent" weight="×1" value={data.breakdown.emails ?? 0} />
          <UsageStat label="InMails sent" weight="×2" value={data.breakdown.inmails ?? 0} />
          <UsageStat label="Sourced" weight="×1" value={data.breakdown.sourced ?? 0} />
        </div>
      </CardContent>
    </Card>
  );
}

function UsageStat({ label, weight, value }: { label: string; weight: string; value: number }) {
  return (
    <div className="rounded-lg border border-border bg-secondary/30 px-3.5 py-3">
      <div className="text-xs text-muted-foreground">
        {label} <span className="opacity-60">{weight}</span>
      </div>
      <div className="mt-0.5 font-display text-xl font-semibold text-foreground">
        {value.toLocaleString()}
      </div>
    </div>
  );
}

function ConnectionsTab() {
  const { data: connections } = useConnections();
  const connect = useConnect();
  const disconnect = useDisconnect();
  const reauth = useReauth();
  const busy = connect.isPending || disconnect.isPending || reauth.isPending;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Connections</CardTitle>
        <span className="text-xs text-muted-foreground">How messages are sent</span>
      </CardHeader>
      <CardContent>
        {!connections ? (
          <Skeleton className="h-32" />
        ) : (
          <>
            {connections.map((c) => {
              const p = PROVIDER[c.provider] ?? { label: c.provider, icon: <Mail className="size-5" /> };
              const st = CONN_STATUS[c.status] ?? { label: c.status, variant: "secondary" as const };
              return (
                <div key={c.id} className="flex items-center gap-4 border-b border-border/60 py-4 last:border-0">
                  <div className="grid size-10 place-items-center rounded-lg border border-border bg-secondary/40" style={{ color: p.color }}>
                    {p.icon}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-semibold text-foreground">{p.label}</div>
                    <div className="text-xs text-muted-foreground">{c.user_email} · {c.seat_type}</div>
                  </div>
                  <Badge variant={st.variant}>{st.label}</Badge>
                  {c.status === "ok" ? (
                    <Button variant="outline" size="sm" disabled={busy} onClick={() => disconnect.mutate(c.id, { onSuccess: () => toast.success("Disconnected") })}>
                      Disconnect
                    </Button>
                  ) : (
                    <Button variant="outline" size="sm" disabled={busy} onClick={() => reauth.mutate(c.id, { onSuccess: () => toast.success("Reconnected") })}>
                      Reconnect
                    </Button>
                  )}
                </div>
              );
            })}
            <div className="flex items-center gap-4 pt-4">
              <div className="grid size-10 place-items-center rounded-lg border border-dashed border-border text-muted-foreground">
                <LinkedInIcon className="size-5" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold text-foreground">LinkedIn · per-recruiter</div>
                <div className="text-xs text-muted-foreground">Connect another seat via Unipile (~150/day)</div>
              </div>
              <Button variant="outline" size="sm" disabled={busy} onClick={() => connect.mutate("linkedin", { onSuccess: () => toast.success("LinkedIn connected") })}>
                Connect
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function ProvidersTab() {
  const { data: providers } = useDataProviders();
  return (
    <Card>
      <CardHeader>
        <CardTitle>Data providers</CardTitle>
        <span className="text-xs text-muted-foreground">Bring your own people-search keys</span>
      </CardHeader>
      <CardContent>
        {!providers ? (
          <Skeleton className="h-32" />
        ) : (
          <>
            {providers.map((p) => (
              <ProviderRow key={p.key} p={p} />
            ))}
            <p className="pt-4 text-xs text-muted-foreground">
              Keys are stored encrypted — only the last four digits are ever shown. Until a key is
              added, people search falls back to demo data.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function providerStatus(p: DataProvider): { label: string; variant: BadgeProps["variant"] } {
  if (!p.configured)
    return p.live
      ? { label: "Not connected", variant: "outline" }
      : { label: "Coming soon", variant: "secondary" };
  if (!p.enabled) return { label: "Disabled", variant: "secondary" };
  if (p.status === "ok") return { label: "Verified", variant: "success" };
  if (p.status === "invalid") return { label: "Invalid key", variant: "warning" };
  return { label: "Connected", variant: "secondary" };
}

function ProviderRow({ p }: { p: DataProvider }) {
  const del = useDeleteDataProvider();
  const verify = useVerifyDataProvider();
  const status = providerStatus(p);
  return (
    <div className="flex items-center gap-4 border-b border-border/60 py-4 last:border-0">
      <div className="grid size-10 place-items-center rounded-lg border border-border bg-secondary/40 text-muted-foreground">
        <Database className="size-5" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-foreground">{p.name}</div>
        <div className="text-xs text-muted-foreground">
          {DATA_PROVIDER_META[p.key] ?? "People data"}
          {p.configured && p.last4 ? ` · ····${p.last4}` : ""}
        </div>
      </div>
      <Badge variant={status.variant}>{status.label}</Badge>
      {p.live && p.configured && (
        <Button
          variant="outline"
          size="sm"
          disabled={verify.isPending}
          onClick={() =>
            verify.mutate(p.key, {
              onSuccess: (r) =>
                r.status === "ok"
                  ? toast.success(`${p.name} key verified`)
                  : toast.error(`${p.name} key looks invalid`),
              onError: () => toast.error("Couldn't verify"),
            })
          }
        >
          Verify
        </Button>
      )}
      {p.live && p.configured && (
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Remove ${p.name} key`}
          className="size-8 text-muted-foreground hover:text-destructive"
          onClick={() => del.mutate(p.key, { onSuccess: () => toast.success(`Removed ${p.name}`) })}
        >
          <Trash2 className="size-4" />
        </Button>
      )}
      {p.live && <KeyDialog provider={p} label={p.configured ? "Update" : "Connect"} />}
    </div>
  );
}

function KeyDialog({ provider, label }: { provider: DataProvider; label: string }) {
  const save = useSaveDataProvider();
  const [apiKey, setApiKey] = React.useState("");

  function submit() {
    if (!apiKey.trim()) return;
    save.mutate(
      { provider: provider.key, body: { api_key: apiKey.trim(), enabled: true } },
      {
        onSuccess: () => {
          toast.success(`${provider.name} key saved`);
          setApiKey("");
        },
        onError: () => toast.error("Couldn't save the key"),
      },
    );
  }

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          {label}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{provider.name} API key</DialogTitle>
        </DialogHeader>
        <div className="grid gap-1.5">
          <Label>API key</Label>
          <Input
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="Paste your key"
          />
          <p className="text-xs text-muted-foreground">
            Stored encrypted; only the last 4 digits are shown after saving.{" "}
            <a href={provider.docs_url} target="_blank" rel="noreferrer" className="underline">
              Where do I find this?
            </a>
          </p>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <DialogClose asChild>
            <Button disabled={save.isPending || !apiKey.trim()} onClick={submit}>
              Save key
            </Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SuppressionTab() {
  const { data: list } = useSuppressions();
  const add = useAddSuppression();
  const remove = useRemoveSuppression();
  const [email, setEmail] = React.useState("");

  function submit() {
    const value = email.trim();
    if (!value) return;
    add.mutate(
      { email: value, reason: "manual" },
      {
        onSuccess: () => {
          toast.success(`Suppressed ${value}`);
          setEmail("");
        },
        onError: () => toast.error("Couldn't add (invalid email?)"),
      },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Do-not-contact</CardTitle>
        <span className="text-xs text-muted-foreground">These addresses are never messaged</span>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2">
          <Input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder="email@company.com"
            aria-label="Email to suppress"
          />
          <Button disabled={add.isPending || !email.trim()} onClick={submit}>
            <Plus /> Add
          </Button>
        </div>
        {!list ? (
          <Skeleton className="h-24" />
        ) : list.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No suppressed addresses. Opt-outs and unsubscribes land here automatically.
          </p>
        ) : (
          <div>
            {list.map((s) => (
              <div
                key={s.id}
                className="flex items-center gap-3 border-b border-border/60 py-2.5 last:border-0"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-foreground">{s.email}</div>
                  <div className="text-xs text-muted-foreground">
                    {s.reason}
                    {s.note ? ` · ${s.note}` : ""}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove ${s.email}`}
                  className="size-8 text-muted-foreground hover:text-destructive"
                  onClick={() =>
                    remove.mutate(s.email, { onSuccess: () => toast.success(`Removed ${s.email}`) })
                  }
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

const ROLE_OPTIONS = ["member", "workspace_admin", "compliance"] as const;

function MembersTab() {
  const { data: members } = useMembers();
  const remove = useRemoveMember();
  const updateRole = useUpdateMemberRole();

  return (
    <Card>
      <CardHeader>
        <CardTitle>Members</CardTitle>
        <InviteDialog />
      </CardHeader>
      <CardContent>
        {!members ? (
          <Skeleton className="h-32" />
        ) : (
          members.map((m) => {
            const role = ROLE[m.role] ?? { label: m.role, variant: "outline" as const };
            return (
              <div key={m.id} className="flex items-center gap-3 border-b border-border/60 py-3 last:border-0">
                <Avatar className="size-9">
                  <AvatarFallback>{initials(m.name)}</AvatarFallback>
                </Avatar>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-semibold text-foreground">{m.name}</div>
                  <div className="text-xs text-muted-foreground">{m.email}</div>
                </div>
                {m.role === "org_admin" ? (
                  <Badge variant={role.variant}>{role.label}</Badge>
                ) : (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button className="outline-none">
                        <Badge variant={role.variant} className="cursor-pointer">
                          {role.label} ▾
                        </Badge>
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      {ROLE_OPTIONS.map((r) => (
                        <DropdownMenuItem
                          key={r}
                          onClick={() => updateRole.mutate({ id: m.id, role: r }, { onSuccess: () => toast.success("Role updated") })}
                        >
                          {ROLE[r].label}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
                {m.role !== "org_admin" && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8 text-muted-foreground hover:text-destructive"
                    onClick={() => remove.mutate(m.id, { onSuccess: () => toast.success(`Removed ${m.name}`) })}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                )}
              </div>
            );
          })
        )}
      </CardContent>
    </Card>
  );
}

function InviteDialog() {
  const invite = useInviteMember();
  const [email, setEmail] = React.useState("");
  const [name, setName] = React.useState("");
  const [role, setRole] = React.useState("member");

  function submit() {
    if (!email.trim() || !name.trim()) return;
    invite.mutate(
      { email, name, role: role as "member" | "workspace_admin" | "compliance" },
      {
        onSuccess: () => {
          toast.success(`Invited ${name}`);
          setEmail("");
          setName("");
        },
        onError: () => toast.error("Couldn't invite (already a member?)"),
      },
    );
  }

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Plus /> Invite
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite a teammate</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid gap-1.5">
            <Label>Name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Dana Okafor" />
          </div>
          <div className="grid gap-1.5">
            <Label>Email</Label>
            <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="dana@company.com" />
          </div>
          <div className="grid gap-1.5">
            <Label>Role</Label>
            <Segmented
              value={role}
              onChange={setRole}
              options={[
                { value: "member", label: "Member" },
                { value: "workspace_admin", label: "Admin" },
                { value: "compliance", label: "Compliance" },
              ]}
            />
          </div>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <DialogClose asChild>
            <Button disabled={invite.isPending || !email.trim() || !name.trim()} onClick={submit}>
              Send invite
            </Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AutonomyTab() {
  const { data } = useWorkspaceSettings();
  const update = useUpdateWorkspaceSettings();
  const s = data?.settings ?? {};

  function set(patch: Record<string, unknown>) {
    update.mutate({ settings: patch }, { onSuccess: () => toast.success("Saved") });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sending controls</CardTitle>
        <span className="text-xs text-muted-foreground">
          <ShieldCheck className="mr-1 inline size-3.5" /> Human-in-the-loop by default
        </span>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-1.5">
          <Label>Default autonomy</Label>
          <Segmented
            value={(s.autonomy_default as string) ?? "approve_each"}
            onChange={(v) => set({ autonomy_default: v })}
            options={[
              { value: "approve_each", label: "Approve each" },
              { value: "auto", label: "Auto-send" },
            ]}
          />
        </div>
        <div className="grid gap-1.5">
          <Label>Business hours window</Label>
          <Input
            key={(s.sending_window as string) ?? ""}
            defaultValue={(s.sending_window as string) ?? ""}
            onBlur={(e) => e.target.value !== s.sending_window && set({ sending_window: e.target.value })}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-1.5">
            <Label>Daily cap · email</Label>
            <Input
              type="number"
              key={String(s.daily_cap_email ?? "")}
              defaultValue={String(s.daily_cap_email ?? 120)}
              onBlur={(e) => set({ daily_cap_email: Number(e.target.value) })}
            />
          </div>
          <div className="grid gap-1.5">
            <Label>Daily cap · LinkedIn</Label>
            <Input
              type="number"
              key={String(s.daily_cap_linkedin ?? "")}
              defaultValue={String(s.daily_cap_linkedin ?? 80)}
              onBlur={(e) => set({ daily_cap_linkedin: Number(e.target.value) })}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
