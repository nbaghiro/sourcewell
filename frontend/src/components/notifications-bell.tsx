import { Bell, CheckCircle2 } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useMarkNotificationsRead, useNotifications } from "@/lib/api/queries";
import { initials, shortAgo } from "@/lib/format";

export function NotificationsBell() {
  const { data } = useNotifications();
  const markRead = useMarkNotificationsRead();
  const navigate = useNavigate();
  const items = data?.items ?? [];
  const unread = data?.unread ?? 0;

  return (
    <DropdownMenu
      onOpenChange={(open) => {
        if (open && unread > 0) markRead.mutate();
      }}
    >
      <DropdownMenuTrigger asChild>
        <button className="relative grid size-9 place-items-center rounded-md border border-border bg-card text-muted-foreground transition-colors hover:text-foreground">
          <Bell className="size-4" />
          {unread > 0 && (
            <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-primary px-1 text-[0.6rem] font-bold text-primary-foreground">
              {unread}
            </span>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        <DropdownMenuLabel className="flex items-center justify-between text-sm font-semibold text-foreground">
          Notifications
        </DropdownMenuLabel>

        {data && data.approvals_waiting > 0 && (
          <>
            <DropdownMenuItem onClick={() => navigate("/approvals")} className="gap-3">
              <span className="grid size-7 place-items-center rounded-md bg-accent">
                <CheckCircle2 className="size-3.5" />
              </span>
              <div className="min-w-0 flex-1 text-sm font-medium text-foreground">
                {data.approvals_waiting} drafts awaiting approval
              </div>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
          </>
        )}

        {items.length === 0 ? (
          <div className="px-3 py-6 text-center text-sm text-muted-foreground">You're all caught up.</div>
        ) : (
          items.map((n) => (
            <DropdownMenuItem key={n.id} onClick={() => navigate("/inbox")} className="gap-3">
              <Avatar className="size-7">
                {n.contact_avatar && <AvatarImage src={n.contact_avatar} alt="" />}
                <AvatarFallback className="text-[0.6rem]">{initials(n.contact_name)}</AvatarFallback>
              </Avatar>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-foreground">{n.title}</div>
                <div className="truncate text-xs text-muted-foreground">{n.body}</div>
              </div>
              <span className="shrink-0 text-[0.65rem] text-muted-foreground">{shortAgo(n.created_at)}</span>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
