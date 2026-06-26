import {
  Columns3,
  Inbox,
  LayoutDashboard,
  LogOut,
  Send,
  Settings,
  Users,
} from "lucide-react";
import { Outlet, useNavigate } from "react-router-dom";

import { AgentChatWidget } from "@/components/agent-chat-widget";
import { AppSidebar, type NavItemDef } from "@/components/app-sidebar";
import { AppShell } from "@/components/app-shell";
import { CommandPalette } from "@/components/command-palette";
import { NotificationsBell } from "@/components/notifications-bell";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { initials } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import { useWorkspace } from "@/lib/workspace";

const NAV: NavItemDef[] = [
  { label: "Home", icon: LayoutDashboard, href: "/" },
  { label: "People", icon: Users, href: "/contacts" },
  { label: "Campaigns", icon: Send, href: "/campaigns" },
  { label: "Inbox", icon: Inbox, href: "/inbox" },
  { label: "Pipeline", icon: Columns3, href: "/pipeline" },
  { label: "Settings", icon: Settings, href: "/settings" },
];

export function AppLayout() {
  const { me, logout } = useAuth();
  const { workspaces, workspaceId, setWorkspaceId } = useWorkspace();
  const navigate = useNavigate();

  const user = me?.user;
  const org = me?.organization;
  const current = workspaces.find((w) => w.id === workspaceId);

  const topbar = (
    <div className="flex items-center justify-between px-6 py-3.5">
      <p className="font-mono text-[0.65rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        {org?.name ?? "Sourcewell"} · {current?.name ?? "—"}
      </p>
      <div className="flex items-center gap-2">
        <CommandPalette />
        <NotificationsBell />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="grid size-9 place-items-center rounded-md bg-primary text-xs font-semibold text-primary-foreground transition-[filter] hover:brightness-110">
              {initials(user?.name)}
            </button>
          </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuLabel>
            <div className="text-sm font-semibold text-foreground">{user?.name}</div>
            <div className="text-xs font-normal text-muted-foreground">{user?.email}</div>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => navigate("/settings")}>
            <Settings /> Settings
          </DropdownMenuItem>
            <DropdownMenuItem onClick={() => void logout()}>
              <LogOut /> Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );

  return (
    <AppShell
      sidebar={
        <AppSidebar
          workspace={current?.name ?? "No workspace"}
          org={org?.name ?? "Sourcewell"}
          items={NAV}
          user={{
            name: user?.name ?? "—",
            role: me?.is_org_admin ? "Org admin" : "Member",
            initials: initials(user?.name),
          }}
          workspaces={workspaces}
          currentWorkspaceId={workspaceId}
          onSelectWorkspace={setWorkspaceId}
        />
      }
      topbar={topbar}
    >
      <Outlet />
      <AgentChatWidget />
    </AppShell>
  );
}
