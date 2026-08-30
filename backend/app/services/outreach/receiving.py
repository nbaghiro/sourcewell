"""Inbound wiring — make sure the provider is actually delivering replies to this deployment.

The mirror of `outreach/messaging.py`: that module puts messages on the wire, this one makes
sure the wire comes back. Unipile only pushes events to URLs you have explicitly subscribed, and
a subscription belongs to the *deployment*, not to a seat — so it has to be asserted somewhere,
or the receiver sits there correctly implemented and permanently silent.

Called on app startup and again whenever a seat connects (the case where a fresh deployment gets
its first user before anything else has run). Idempotent, and fail-soft: a provider that's down
must never break a sign-in or stop the API from booting.
"""

from app.core.config import get_settings
from app.core.logging import logger
from app.ext.unipile import unipile_connection

# LinkedIn DMs, connected-mailbox replies, and seat credential/disconnect events.
_SOURCES = ("messaging", "email", "account")


def receiver_url() -> str | None:
    """The public URL Unipile should POST inbound events to, or None when unconfigured.

    The shared secret rides in the query string because that's the only part of the subscription
    Unipile lets us control; `/webhooks/unipile` also accepts it as `X-Unipile-Token`.
    """
    s = get_settings()
    if not (s.unipile_api_key and s.unipile_dsn and s.unipile_webhook_secret and s.api_base_url):
        return None
    return f"{s.api_base_url.rstrip('/')}/webhooks/unipile?token={s.unipile_webhook_secret}"


async def ensure_inbound_webhooks() -> dict[str, str]:
    """Subscribe the receiver for every event source. Returns `{source: outcome}` for logging.

    Outcomes: `registered` (created now), `present` (already subscribed), `failed` (provider
    rejected it), `skipped` (nothing configured to register against).
    """
    url = receiver_url()
    conn = unipile_connection()
    if url is None or conn is None:
        return dict.fromkeys(_SOURCES, "skipped")

    existing = await conn.list_webhooks()
    # None = we couldn't read the list; register blindly rather than risk registering nothing.
    already = {source for reg_url, source in existing or [] if reg_url == url}

    results: dict[str, str] = {}
    for source in _SOURCES:
        if source in already:
            results[source] = "present"
            continue
        try:
            created = await conn.register_webhooks(request_url=url, source=source)
            results[source] = "registered" if created else "failed"
        except Exception as exc:
            # An unreachable provider at boot is an operational condition, not a bug — one warning
            # line per source, not three stack traces. The caller summarises.
            logger.warning("unipile: could not register the %s webhook (%s)", source, exc)
            results[source] = "failed"
    return results


async def ensure_inbound_webhooks_quietly() -> None:
    """`ensure_inbound_webhooks` for callers that only want the side effect and a log line."""
    try:
        results = await ensure_inbound_webhooks()
    except Exception:
        logger.exception("unipile: inbound webhook registration failed")
        return
    created = [source for source, outcome in results.items() if outcome == "registered"]
    failed = [source for source, outcome in results.items() if outcome == "failed"]
    if created:
        logger.info("unipile: registered inbound webhook(s) for %s", ", ".join(created))
    if failed:
        logger.warning("unipile: could not register inbound webhook(s) for %s", ", ".join(failed))
