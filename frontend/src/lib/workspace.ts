import * as React from "react";

import { api } from "@/lib/api";

export interface WorkspaceLite {
  id: string;
  name: string;
  kind: string;
}

interface WorkspaceCtx {
  workspaceId: string | null;
  setWorkspaceId: (id: string) => void;
  workspaces: WorkspaceLite[];
}

export const WorkspaceContext = React.createContext<WorkspaceCtx | null>(null);

export function useWorkspace(): WorkspaceCtx {
  const ctx = React.useContext(WorkspaceContext);
  if (!ctx) return { workspaceId: null, setWorkspaceId: () => {}, workspaces: [] };
  return ctx;
}

/** The active workspace id (from the switcher). */
export function useWorkspaceId(): string | null {
  return React.useContext(WorkspaceContext)?.workspaceId ?? null;
}

interface State<T> {
  data: T | null;
  loading: boolean;
  error: boolean;
  reload: () => void;
}

/** Fetch a workspace-scoped endpoint (sends X-Workspace-Id), with a manual reload. */
export function useWorkspaceData<T>(path: string): State<T> {
  const wsId = useWorkspaceId();
  const [data, setData] = React.useState<T | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(false);
  const [nonce, setNonce] = React.useState(0);

  React.useEffect(() => {
    if (!wsId) return;
    let on = true;
    setLoading(true);
    setError(false);
    api<T>(path, { headers: { "X-Workspace-Id": wsId } })
      .then((d) => on && setData(d))
      .catch(() => {
        if (on) {
          setData(null);
          setError(true);
        }
      })
      .finally(() => on && setLoading(false));
    return () => {
      on = false;
    };
  }, [wsId, path, nonce]);

  return { data, loading, error, reload: () => setNonce((n) => n + 1) };
}

/** POST/mutation against a workspace-scoped endpoint. */
export async function workspacePost<T>(
  path: string,
  wsId: string,
  init: RequestInit = {},
): Promise<T> {
  return api<T>(path, {
    method: "POST",
    ...init,
    headers: { "X-Workspace-Id": wsId, ...init.headers },
  });
}
