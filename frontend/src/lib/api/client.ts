import createClient, { type Middleware } from "openapi-fetch";

import { API_URL } from "@/lib/api";
import { appFetch } from "./demo/transport";
import type { paths } from "./schema";

// The middleware can't read React context, so the active workspace id is mirrored here by
// WorkspaceProvider and injected as X-Workspace-Id on every request.
let currentWorkspaceId: string | null = null;
export function setApiWorkspaceId(id: string | null) {
  currentWorkspaceId = id;
}

const workspaceMiddleware: Middleware = {
  onRequest({ request }) {
    if (currentWorkspaceId) request.headers.set("X-Workspace-Id", currentWorkspaceId);
    return request;
  },
};

/** Fully-typed API client generated from the backend's OpenAPI schema. */
export const client = createClient<paths>({ baseUrl: API_URL, credentials: "include", fetch: appFetch });
client.use(workspaceMiddleware);

/** Narrow openapi-fetch's {data,error} result to data, throwing on error (for react-query). */
export function unwrap<T>(result: { data?: T; error?: unknown }): T {
  if (result.error) throw result.error;
  return result.data as T;
}
