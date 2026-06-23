import * as React from "react";

import { setApiWorkspaceId } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { WorkspaceContext } from "@/lib/workspace";

const KEY = "sw_workspace";

/** Holds the active workspace selection (persisted), shared across all scoped pages. */
export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const { me } = useAuth();
  const workspaces = React.useMemo(() => me?.workspaces ?? [], [me]);
  const [picked, setPicked] = React.useState<string | null>(() => localStorage.getItem(KEY));

  const workspaceId =
    picked && workspaces.some((w) => w.id === picked) ? picked : (workspaces[0]?.id ?? null);

  const setWorkspaceId = React.useCallback((id: string) => {
    localStorage.setItem(KEY, id);
    setPicked(id);
  }, []);

  // Mirror the active workspace into the API client's X-Workspace-Id middleware SYNCHRONOUSLY during
  // render — an effect would run after React Query has already refired queries for the new workspace
  // key, sending the previous workspace's header and caching the wrong data under the new key.
  setApiWorkspaceId(workspaceId);

  const value = React.useMemo(
    () => ({ workspaceId, setWorkspaceId, workspaces }),
    [workspaceId, setWorkspaceId, workspaces],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}
