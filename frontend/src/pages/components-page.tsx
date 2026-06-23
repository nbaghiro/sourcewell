import {
  Bell,
  CheckCircle2,
  Columns3,
  Inbox,
  LayoutDashboard,
  Pause,
  Pencil,
  Plus,
  SearchX,
  Send,
  Settings,
  Trash2,
  Users,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/app-shell";
import { AppSidebar, type NavItemDef } from "@/components/app-sidebar";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PersonCell } from "@/components/person-cell";
import { ScoreBar } from "@/components/score-bar";
import { StatCard } from "@/components/stat-card";
import { StateBadge } from "@/components/state-badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

const NAV: NavItemDef[] = [
  { label: "Dashboard", icon: LayoutDashboard },
  { label: "Contacts", icon: Users },
  { label: "Campaigns", icon: Send },
  { label: "Approvals", icon: CheckCircle2, count: 12 },
  { label: "Inbox", icon: Inbox, count: 3 },
  { label: "Pipeline", icon: Columns3, active: true },
  { label: "Settings", icon: Settings },
];

const SWATCHES: [string, string][] = [
  ["background", "bg-background"],
  ["card", "bg-card"],
  ["primary", "bg-primary"],
  ["secondary", "bg-secondary"],
  ["muted", "bg-muted"],
  ["accent", "bg-accent"],
  ["success", "bg-success"],
  ["warning", "bg-warning"],
  ["destructive", "bg-destructive"],
  ["sidebar", "bg-sidebar"],
  ["border", "bg-border"],
  ["ring", "bg-ring"],
];

const STATES = [
  "active",
  "paused",
  "draft",
  "proposed",
  "awaiting_approval",
  "scheduled",
  "awaiting_reply",
  "handed_off",
  "opted_out",
  "interested",
];

const CANDIDATES = [
  { name: "Jane Doe", title: "Senior Backend Engineer · Acme", score: 96, state: "scheduled" },
  { name: "Marcus Lee", title: "Staff Engineer · Globex", score: 88, state: "awaiting_reply" },
  { name: "Priya Nair", title: "Platform Engineer · Umbrella", score: 81, state: "handed_off" },
  { name: "Diego Santos", title: "Backend Engineer · Initech", score: 64, state: "proposed" },
];

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-4">
      <div>
        <h2 className="font-display text-lg font-semibold tracking-tight text-foreground">{title}</h2>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}

function Demo({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2.5">
      <p className="font-mono text-[0.6rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card p-5">
        {children}
      </div>
    </div>
  );
}

export function ComponentsPage() {
  const topbar = (
    <div className="flex items-center justify-between px-7 py-4">
      <div>
        <p className="font-mono text-[0.6rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          Design system
        </p>
        <h1 className="font-display text-xl font-bold tracking-tight text-foreground">
          Component Library
        </h1>
      </div>
      <div className="flex items-center gap-2">
        <Badge variant="accent">Wellspring</Badge>
        <Button
          variant="outline"
          size="sm"
          onClick={() => toast.success("Sent to Jane Doe", { description: "First-touch email delivered." })}
        >
          Test toast
        </Button>
      </div>
    </div>
  );

  return (
    <AppShell
      sidebar={
        <AppSidebar
          workspace="Backend Hiring"
          items={NAV}
          user={{ name: "Avery Brooks", role: "Recruiting lead", initials: "AB" }}
        />
      }
      topbar={topbar}
    >
      <div className="mx-auto max-w-5xl space-y-12">
        <PageHeader
          eyebrow="Sourcewell"
          title="Building blocks"
          description="The shared base + composite components every page is assembled from. All styling flows from the Wellspring token block in index.css — swap it to re-skin everything."
        />

        {/* ---------------- Foundations ---------------- */}
        <Section title="Foundations" description="Color tokens, type scale, and radius — the design language itself.">
          <Demo label="Color tokens">
            <div className="grid w-full grid-cols-3 gap-4 sm:grid-cols-4 md:grid-cols-6">
              {SWATCHES.map(([name, cls]) => (
                <div key={name} className="space-y-1.5">
                  <div className={`h-12 rounded-lg border border-border ${cls}`} />
                  <div className="text-xs font-medium text-foreground">{name}</div>
                </div>
              ))}
            </div>
          </Demo>
          <Demo label="Typography">
            <div className="space-y-3">
              <p className="font-display text-3xl font-bold tracking-tight text-foreground">
                Bricolage Grotesque — display
              </p>
              <p className="font-sans text-base text-foreground">
                Hanken Grotesk — interface body text, calm and legible at small sizes.
              </p>
              <p className="font-mono text-sm text-muted-foreground">
                Geist Mono — 0123456789 · scores, IDs, timestamps
              </p>
            </div>
          </Demo>
          <Demo label="Radius">
            <div className="flex items-end gap-4">
              {["rounded-sm", "rounded-md", "rounded-lg", "rounded-xl"].map((r) => (
                <div key={r} className="space-y-1.5 text-center">
                  <div className={`size-16 border border-border bg-accent ${r}`} />
                  <div className="text-xs text-muted-foreground">{r}</div>
                </div>
              ))}
            </div>
          </Demo>
        </Section>

        {/* ---------------- Base ---------------- */}
        <Section title="Base components" description="The shadcn / Radix primitives, themed to Wellspring.">
          <Demo label="Button — variants">
            <Button>Approve &amp; send</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="outline">Outline</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="destructive">Delete</Button>
            <Button variant="link">Link</Button>
          </Demo>
          <Demo label="Button — sizes & icons">
            <Button size="sm">Small</Button>
            <Button>
              <Plus /> New campaign
            </Button>
            <Button size="lg">Large</Button>
            <Button size="icon" variant="outline">
              <Bell />
            </Button>
            <Button disabled>Disabled</Button>
          </Demo>

          <Demo label="Inputs">
            <div className="grid w-full max-w-md gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="email">From email</Label>
                <Input id="email" placeholder="recruiter@acme.com" />
              </div>
              <div className="flex items-center gap-6">
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox defaultChecked /> Skip recent contacts
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <Switch defaultChecked /> Auto-send
                </label>
              </div>
              <Select defaultValue="approve_each">
                <SelectTrigger className="w-64">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="approve_each">Approve each message</SelectItem>
                  <SelectItem value="auto">Auto-send</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </Demo>

          <Demo label="Badges">
            <Badge>Default</Badge>
            <Badge variant="secondary">Secondary</Badge>
            <Badge variant="accent">Accent</Badge>
            <Badge variant="outline">Outline</Badge>
            <Badge variant="success">Interested</Badge>
            <Badge variant="warning">Awaiting</Badge>
            <Badge variant="destructive">Opted out</Badge>
          </Demo>

          <Demo label="Avatar · Separator · Skeleton">
            <Avatar>
              <AvatarFallback>AB</AvatarFallback>
            </Avatar>
            <Separator orientation="vertical" className="h-8" />
            <div className="grid gap-2">
              <Skeleton className="h-3 w-40" />
              <Skeleton className="h-3 w-24" />
            </div>
          </Demo>

          <Demo label="Tabs">
            <Tabs defaultValue="proposed" className="w-full">
              <TabsList>
                <TabsTrigger value="proposed">Proposed</TabsTrigger>
                <TabsTrigger value="active">Active</TabsTrigger>
                <TabsTrigger value="replied">Replied</TabsTrigger>
              </TabsList>
              <TabsContent value="proposed" className="text-sm text-muted-foreground">
                12 proposed leads awaiting your review.
              </TabsContent>
              <TabsContent value="active" className="text-sm text-muted-foreground">
                34 candidates currently in sequence.
              </TabsContent>
              <TabsContent value="replied" className="text-sm text-muted-foreground">
                7 replies in the last 7 days.
              </TabsContent>
            </Tabs>
          </Demo>

          <Demo label="Overlays — Dialog · Dropdown · Tooltip">
            <Dialog>
              <DialogTrigger asChild>
                <Button variant="outline">Open dialog</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Approve &amp; send</DialogTitle>
                  <DialogDescription>
                    Send this first-touch email to Jane Doe? It will go out during EU business hours.
                  </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  <DialogClose asChild>
                    <Button variant="ghost">Cancel</Button>
                  </DialogClose>
                  <Button>Approve &amp; send</Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline">Actions</Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuLabel>Campaign</DropdownMenuLabel>
                <DropdownMenuItem>
                  <Pencil /> Edit
                </DropdownMenuItem>
                <DropdownMenuItem>
                  <Pause /> Pause
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem>
                  <Trash2 /> Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="outline" size="icon">
                  <Bell />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Notifications</TooltipContent>
            </Tooltip>
          </Demo>

          <Demo label="Table">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Candidate</TableHead>
                  <TableHead>Fit</TableHead>
                  <TableHead>State</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {CANDIDATES.map((c) => (
                  <TableRow key={c.name}>
                    <TableCell>
                      <PersonCell name={c.name} subtitle={c.title} />
                    </TableCell>
                    <TableCell>
                      <ScoreBar value={c.score} />
                    </TableCell>
                    <TableCell>
                      <StateBadge state={c.state} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Demo>
        </Section>

        {/* ---------------- Composite ---------------- */}
        <Section
          title="Composite components"
          description="App-specific building blocks assembled from the base set."
        >
          <Demo label="StatCard">
            <div className="grid w-full grid-cols-2 gap-4 md:grid-cols-4">
              <StatCard label="Active campaigns" value={4} trend="1 launched" />
              <StatCard label="Contacts sourced" value="1,284" trend="96 this week" />
              <StatCard label="Awaiting approval" value={12} trend="needs review" trendDirection="flat" />
              <StatCard label="Replies · 7d" value={7} trend="3 interested" />
            </div>
          </Demo>

          <Demo label="ScoreBar">
            <div className="grid gap-3">
              {[96, 81, 64, 38].map((v) => (
                <ScoreBar key={v} value={v} />
              ))}
            </div>
          </Demo>

          <Demo label="StateBadge — full set">
            {STATES.map((s) => (
              <StateBadge key={s} state={s} />
            ))}
          </Demo>

          <Demo label="PersonCell · Card">
            <Card className="w-full max-w-sm">
              <CardHeader>
                <div>
                  <CardTitle>Jane Doe</CardTitle>
                  <CardDescription>Senior Backend Engineer · Acme</CardDescription>
                </div>
                <StateBadge state="scheduled" />
              </CardHeader>
              <CardContent>
                <PersonCell name="Jane Doe" subtitle="jane@example.com · Berlin" />
              </CardContent>
            </Card>
          </Demo>

          <Demo label="EmptyState">
            <EmptyState
              className="w-full"
              icon={SearchX}
              title="No contacts yet"
              description="Import a CSV or generate a sample set to start ranking candidates."
              action={
                <Button>
                  <Plus /> Generate sample
                </Button>
              }
            />
          </Demo>

          <Demo label="AppSidebar">
            <div className="h-[460px] w-60 overflow-hidden rounded-xl border border-border">
              <AppSidebar
                className="h-full"
                workspace="Backend Hiring"
                items={NAV}
                user={{ name: "Avery Brooks", role: "Recruiting lead", initials: "AB" }}
              />
            </div>
          </Demo>
        </Section>
      </div>
    </AppShell>
  );
}
