import * as React from "react";

import { getApiWorkspaceId, setApiOrganizationId, setApiWorkspaceId } from "@/lib/api/tenant";
import { useAuth } from "@/lib/auth";
import { WorkspaceContext } from "@/lib/workspace";

/** Holds the active workspace selection (persisted), shared across all scoped pages. */
export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const { me } = useAuth();
  const workspaces = React.useMemo(() => me?.workspaces ?? [], [me]);
  const [picked, setPicked] = React.useState<string | null>(() => getApiWorkspaceId());

  const workspaceId =
    picked && workspaces.some((w) => w.id === picked) ? picked : (workspaces[0]?.id ?? null);

  const setWorkspaceId = React.useCallback((id: string) => setPicked(id), []);

  // Mirror the active tenant into the API client's header middleware SYNCHRONOUSLY during render —
  // an effect would run after React Query has already refired queries for the new workspace key,
  // sending the previous workspace's header and caching the wrong data under the new key.
  setApiWorkspaceId(workspaceId);
  setApiOrganizationId(
    workspaces.find((w) => w.id === workspaceId)?.organization_id ??
      me?.organization?.id ??
      null,
  );

  const value = React.useMemo(
    () => ({ workspaceId, setWorkspaceId, workspaces }),
    [workspaceId, setWorkspaceId, workspaces],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}
