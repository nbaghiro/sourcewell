/**
 * The active tenant, mirrored outside React so plain `fetch` callers and the openapi-fetch
 * middleware can both stamp it on a request. WorkspaceProvider writes it during render; both values
 * are seeded from localStorage so the first request of a session (/auth/me, before the provider has
 * rendered) already names the tenant the user last worked in.
 */

const WORKSPACE_KEY = "sw_workspace";
const ORG_KEY = "sw_organization";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // A browser with site data blocked still works, it just forgets the selection.
  }
}

let workspaceId: string | null = read(WORKSPACE_KEY);
let organizationId: string | null = read(ORG_KEY);

export function setApiWorkspaceId(id: string | null) {
  workspaceId = id;
  write(WORKSPACE_KEY, id);
}

/** The active workspace id (for hand-rolled fetches like the SSE chat stream). */
export function getApiWorkspaceId(): string | null {
  return workspaceId;
}

export function setApiOrganizationId(id: string | null) {
  organizationId = id;
  write(ORG_KEY, id);
}

/** The active organization id — disambiguates the tenant for a user who belongs to several. */
export function getApiOrganizationId(): string | null {
  return organizationId;
}

/** Forget the remembered tenant (a stale selection the server no longer accepts). */
export function clearApiTenant() {
  setApiWorkspaceId(null);
  setApiOrganizationId(null);
}

/** The tenant headers every request carries. */
export function tenantHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (workspaceId) headers["X-Workspace-Id"] = workspaceId;
  if (organizationId) headers["X-Organization-Id"] = organizationId;
  return headers;
}
