/** Base URL of the FastAPI backend. Override with VITE_API_URL for other environments. */
export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8901";

/** fetch wrapper that always sends the session cookie and JSON-decodes the result. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    throw new ApiError(res.status, await res.text().catch(() => res.statusText));
  }
  return (await res.json()) as T;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
