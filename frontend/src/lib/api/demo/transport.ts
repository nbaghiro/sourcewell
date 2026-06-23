/**
 * Demo transport: a fetch-compatible function that answers requests from the in-memory demo store
 * instead of the network. Both the typed `client` (openapi-fetch) and the hand-written `api()` route
 * through `appFetch`, so flipping DEMO_MODE here is the ONLY change needed to swap the whole app
 * between demo data and the real backend.
 */
import { handle } from "./handlers";

/**
 * The single switch. Default = the real backend (which now serves the seeded 3-vertical demo org).
 * Set VITE_DEMO=1 to use the in-memory client mock instead (e.g. a standalone deploy with no backend).
 */
export const DEMO_MODE =
  import.meta.env.VITE_DEMO === "1" || import.meta.env.VITE_DEMO === "true";

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

async function demoFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  let method = "GET";
  let rawUrl = "";
  let headers = new Headers();
  let bodyText = "";

  if (input instanceof Request) {
    method = input.method;
    rawUrl = input.url;
    headers = new Headers(input.headers);
    bodyText = await input.clone().text().catch(() => "");
  } else {
    rawUrl = String(input);
    method = (init?.method ?? "GET").toUpperCase();
    headers = new Headers((init?.headers as HeadersInit) ?? {});
    if (typeof init?.body === "string") bodyText = init.body;
  }

  const u = new URL(rawUrl, "http://demo.local");
  // A touch of latency so loading states are visible; sign-in is slower to simulate the real auth
  // round-trip (and show the loader screen) before the demo account appears.
  const ms = u.pathname === "/auth/dev-login" ? 850 : u.pathname === "/auth/me" ? 250 : 120;
  await delay(ms);
  const { status, data } = handle(method.toUpperCase(), u.pathname, {
    wsId: headers.get("X-Workspace-Id"),
    query: u.searchParams,
    body: bodyText ? safeJson(bodyText) : undefined,
  });
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** What the rest of the app uses for transport. */
export const appFetch: typeof fetch = DEMO_MODE ? (demoFetch as typeof fetch) : globalThis.fetch.bind(globalThis);
