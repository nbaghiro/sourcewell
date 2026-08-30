import createClient, { type Middleware } from "openapi-fetch";

import { API_URL } from "@/lib/api";
import { tenantHeaders } from "./tenant";
import type { paths } from "./schema";

const tenantMiddleware: Middleware = {
  onRequest({ request }) {
    for (const [key, value] of Object.entries(tenantHeaders())) request.headers.set(key, value);
    return request;
  },
};

/** Fully-typed API client generated from the backend's OpenAPI schema. */
export const client = createClient<paths>({ baseUrl: API_URL, credentials: "include" });
client.use(tenantMiddleware);

/** Narrow openapi-fetch's {data,error} result to data, throwing on error (for react-query). */
export function unwrap<T>(result: { data?: T; error?: unknown }): T {
  if (result.error) throw result.error;
  return result.data as T;
}

/** The human-readable message behind an API failure (FastAPI's `detail`), or a fallback. */
export function apiErrorMessage(error: unknown, fallback: string): string {
  if (typeof error === "object" && error !== null && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return fallback;
}
