"""Inbound wiring — make sure the provider is actually delivering replies to this deployment.

The mirror of `outreach/messaging.py`: that module puts messages on the wire, this one makes
sure the wire comes back. Unipile only pushes events to URLs you have explicitly subscribed, and
a subscription belongs to the *deployment*, not to a seat — so it has to be asserted somewhere,
or the receiver sits there correctly implemented and permanently silent.

Called on app startup and again whenever a seat connects (the case where a fresh deployment gets
its first user before anything else has run). Idempotent, and fail-soft: a provider that's down
must never break a sign-in or stop the API from booting.
"""

from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import logger
from app.core.types import JsonObject
from app.ext.unipile import unipile_connection
from app.models import (
    ORG_WIDE_ROLES,
    Channel,
    Connection,
    ConnectionStatus,
    Contact,
    Enrollment,
    Membership,
    Message,
    SpaceGrant,
    Workspace,
)
from app.services.insights import audit
from app.services.outreach.messaging import record_inbound

# LinkedIn DMs, connected-mailbox replies, and seat credential/disconnect events.
# `account_status`, not `account` — Unipile answers the latter with a 400, so the third
# subscription was silently never created and no seat-disconnect event ever reached us. Nothing
# surfaced it: `register_webhooks` returns False on a rejection and the caller only logs a summary.
_SOURCES = ("messaging", "email", "account_status")


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


# --- What the person actually wrote -------------------------------------------

# Lines that open a quoted history. Everything below one is a copy of the message we already have
# on the thread.
_QUOTE_BREAKS = (
    "-----original message-----",
    "--------- forwarded message ---------",
    "-------- forwarded message --------",
    "________________________________",
)


def strip_quoted_reply(text: str) -> str:
    """Just the part of an email reply the person actually wrote.

    Mail clients quote the whole conversation back at you, so a reply arrives carrying every
    message that preceded it — including our own unsubscribe footer. Stored whole, the thread
    shows itself twice and, worse, `classify_reply` reads *our* words as the candidate's: a reply
    saying "lets try it out" was classified `opted_out`, because the quote below it contained
    "Not interested? Unsubscribe:".

    Deliberately conservative: it cuts at the first quote marker and drops the attribution line
    above it only when that line carries an address (`On …, x@y.com wrote:`). A line that merely
    ends in a colon — "Here's my question:" — is left alone, because eating real content is a far
    worse failure than leaving a stray line of quoting.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    cut = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(">") or stripped.lower().startswith(_QUOTE_BREAKS):
            cut = i
            break
    if cut is None:
        return text.strip()

    kept = lines[:cut]
    while kept and not kept[-1].strip():
        kept.pop()
    # "On 31 Aug 2026, at 15:14, someone@example.com wrote:" belongs to the quote, not the reply.
    if kept and kept[-1].rstrip().endswith(":") and "@" in kept[-1]:
        kept.pop()
        while kept and not kept[-1].strip():
            kept.pop()

    body = "\n".join(kept).strip()
    # A reply that is *only* a quote (or that we misread) keeps its original text: a noisy message
    # on the thread is recoverable, an empty one is not.
    return body or text.strip()


# --- Interpreting a provider event -------------------------------------------


def _is_own_message(payload: JsonObject) -> bool:
    """Did this seat send the message the provider is telling us about?

    Unipile pushes *every* message in a chat, including the ones we sent — so without this the
    receiver records our own outreach as the candidate's reply: a fabricated inbound bubble, a
    sequence stuck waiting on a reply that already "arrived", and at full autonomy an agent
    answering its own message. `is_sender` is the provider's own flag for it.
    """
    for source in (payload.get("message"), payload):
        if not isinstance(source, dict):
            continue
        flag = source.get("is_sender")
        if isinstance(flag, bool):
            return flag
        if isinstance(flag, int):
            return flag == 1
        if isinstance(flag, str) and flag.strip().lower() in {"true", "1"}:
            return True
    return False


def idempotency_key(payload: JsonObject, *, thread_id: str | None, text: str) -> str | None:
    """A stable id for this inbound event, so a redelivery is recognised and dropped.

    Prefer the provider's own message id. When there isn't one, fall back to a digest of the
    event's identifying parts *including the provider timestamp* — a redelivery hashes the same,
    while a candidate genuinely sending "ok" twice does not (different timestamps). With neither
    an id nor a timestamp the digest can't separate those two cases, so we return None and record
    unconditionally: a rare duplicate bubble beats silently dropping a real reply.
    """
    message_id = payload_str(payload.get("message"), "id", "message_id") or payload_str(
        payload, "message_id", "id"
    )
    if message_id:
        return message_id
    stamp = payload_str(payload.get("message"), "timestamp", "date", "created_at") or payload_str(
        payload, "timestamp", "date", "created_at"
    )
    if not stamp:
        return None
    digest = sha256("\x00".join([thread_id or "", stamp, text]).encode()).hexdigest()
    return f"sha256:{digest}"


def payload_str(obj: object, *keys: str) -> str | None:
    """First non-empty string value among `keys` of a dict-ish payload, else None."""
    if not isinstance(obj, dict):
        return None
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    return None


async def _seat_workspace_ids(session: AsyncSession, account_id: str | None) -> list[str] | None:
    """Workspaces the seat that received this event can send from, or None when it's unknown.

    Mirrors the access rules in `api/context.py`: the seat's owner reaches every workspace in an
    organization where they are org_admin or compliance, plus the ones an explicit grant names.
    `None` means we couldn't identify the seat at all — the caller then has to fall back to a
    weaker rule rather than a wrong one.
    """
    if not account_id:
        return None
    org_wide = (
        select(Membership.organization_id)
        .join(Connection, Connection.user_id == Membership.user_id)
        .where(Connection.external_id == account_id, Membership.role.in_(ORG_WIDE_ROLES))
    )
    granted = (
        select(SpaceGrant.workspace_id)
        .join(Connection, Connection.user_id == SpaceGrant.user_id)
        .where(Connection.external_id == account_id)
    )
    rows = (
        await session.execute(
            select(Workspace.id).where(
                or_(Workspace.organization_id.in_(org_wide), Workspace.id.in_(granted))
            )
        )
    ).scalars()
    return list(dict.fromkeys(rows)) or None


async def _resolve_enrollment(
    session: AsyncSession,
    *,
    external_id: str | None,
    sender_email: str | None,
    account_id: str | None = None,
) -> tuple[Enrollment, Channel] | None:
    """Map a Unipile event to (enrollment, channel): the chat we sent on, else the sender address.

    The channel comes from the outbound message the thread id matched, so a LinkedIn reply is
    recorded as LinkedIn rather than defaulting to email.

    Both lookups are scoped to the workspaces of the seat that received the event. Without that
    scope the address fallback spans every tenant, and two customers working the same candidate
    cross over — one of them silently receiving the other's reply.
    """
    scope = await _seat_workspace_ids(session, account_id)
    if external_id:
        stmt = select(Message).where(Message.external_id == external_id)
        if scope is not None:
            stmt = stmt.where(Message.workspace_id.in_(scope))
        msg = (
            (await session.execute(stmt.order_by(Message.created_at.desc()).limit(1)))
            .scalars()
            .first()
        )
        if msg is not None:
            enrollment = await session.get(Enrollment, msg.enrollment_id)
            if enrollment is not None:
                return enrollment, msg.channel
    if sender_email:
        by_sender = (
            select(Enrollment)
            .join(Contact, Enrollment.contact_id == Contact.id)
            .where(func.lower(Contact.email) == sender_email.strip().lower())
        )
        if scope is not None:
            by_sender = by_sender.where(Enrollment.workspace_id.in_(scope))
        candidates = list(
            (await session.execute(by_sender.order_by(Enrollment.created_at.desc())))
            .scalars()
            .all()
        )
        enrollment = _unambiguous(candidates, sender_email)
        if enrollment is not None:
            # No thread id to read a channel off: this arrived at the email receiver.
            return enrollment, Channel.email
    return None


def _unambiguous(candidates: list[Enrollment], sender_email: str) -> Enrollment | None:
    """The newest candidate — unless they straddle workspaces, in which case there is no answer.

    Guessing across a tenant boundary is the one outcome worse than dropping the reply: it shows
    one customer's candidate reply inside another's inbox. Ambiguity here means the event carried
    no seat we could scope by, so we log it loudly instead of picking.
    """
    if not candidates:
        return None
    workspaces = {e.workspace_id for e in candidates}
    if len(workspaces) > 1:
        logger.warning(
            "inbound: %d enrollments across %d workspaces match sender %s — dropping, "
            "no seat to disambiguate",
            len(candidates),
            len(workspaces),
            sender_email,
        )
        return None
    return candidates[0]


def text_of(payload: JsonObject) -> str | None:
    """The message text in a provider event, whichever shape it arrived in.

    `body_plain` before `body`: an email carries both, and `body` is the HTML part — recording
    that puts markup on the thread and feeds tags to the reply classifier.
    """
    return (
        payload_str(payload.get("message"), "text", "body")
        or payload_str(payload, "body_plain")
        or payload_str(payload, "text", "body", "message")
    )


def sender_of(payload: JsonObject) -> str | None:
    """The sender's address, however this channel names it.

    Unipile names it differently per channel: an email carries `from_attendee`
    (`{display_name, identifier}`), a chat message a `sender`.
    """
    return (
        payload_str(payload.get("from_attendee"), "identifier", "email")
        or payload_str(payload.get("sender"), "email", "identifier")
        or payload_str(payload, "from", "from_email")
    )


def _dropped(why: str, payload: JsonObject) -> str:
    """Log an inbound event we couldn't place, and report it as ignored."""
    logger.warning("inbound: dropping an event — %s; payload keys=%s", why, sorted(payload))
    return "ignored"


async def record_provider_event(
    session: AsyncSession, *, payload: JsonObject, now: datetime
) -> str:
    """Record one inbound provider message. Returns the outcome for the caller to report.

    The single place a Unipile message becomes a `Message` row, whether it arrived on the webhook
    or was swept up afterwards. Recording only: classification and the Outreach agent run on the
    worker, so a redelivery — or a sweep overlapping a webhook — can never answer a candidate
    twice. `provider_message_id` is what makes that overlap safe.
    """
    text = text_of(payload)
    if not text:
        return _dropped("no text in the event", payload)
    if _is_own_message(payload):
        # Our own outbound, echoed back by the provider. Recording it would invent a reply.
        return "own_message"
    chat_id = payload_str(payload, "chat_id", "chat", "thread_id")
    sender_email = sender_of(payload)
    account_id = payload_str(payload, "account_id", "account")
    resolved = await _resolve_enrollment(
        session, external_id=chat_id, sender_email=sender_email, account_id=account_id
    )
    if resolved is None:
        return _dropped(
            f"no thread matched (chat_id={chat_id!r}, sender={sender_email!r})", payload
        )
    enrollment, matched_channel = resolved
    message = await record_inbound(
        session,
        enrollment=enrollment,
        # Email only: a LinkedIn DM has no quoted history, and a chat message that happens to
        # start a line with ">" would be truncated for nothing.
        text=strip_quoted_reply(text) if matched_channel is Channel.email else text,
        now=now,
        channel=matched_channel,
        provider_message_id=idempotency_key(payload, thread_id=chat_id, text=text),
        external_id=chat_id,
    )
    return "duplicate" if message is None else "queued"


# --- Account lifecycle --------------------------------------------------------

# Unipile's `account_status` event names. Matched exactly, against an allow-list.
#
# This used to be a substring test for "credential", "disconnect" or "error" against whichever of
# `event`/`type`/`status` came first — and `error` is far too broad for a field that also carries
# per-message delivery status. A message event reporting an error would be read as a seat
# disconnect: the seat flipped to needs-reauth and the candidate's message was dropped.
_ACCOUNT_EVENTS = frozenset(
    {
        "credentials",
        "account_credentials",
        "creation_fail",
        "disconnected",
        "account_disconnected",
        "permissions",
        "sync_error",
        "error",
    }
)


async def record_account_event(
    session: AsyncSession, *, payload: JsonObject, now: datetime
) -> str | None:
    """Handle a seat credential/disconnect event. Returns the outcome, or None if this isn't one.

    None means "not an account event" — the caller should go on to try it as a message. An event
    carrying a message body is never treated as an account event, whatever its type says.
    """
    account_id = payload_str(payload, "account_id", "account")
    event = (payload_str(payload, "event", "type", "status") or "").strip().lower()
    if not account_id or event not in _ACCOUNT_EVENTS or text_of(payload):
        return None

    # Every connection holding this account, not just one. `upsert_seat` keys on (user, provider),
    # so two people can have connected the same provider account; flipping an arbitrary one left
    # the other advertising itself as healthy and failing every send it was picked for.
    seats = (
        (await session.execute(select(Connection).where(Connection.external_id == account_id)))
        .scalars()
        .all()
    )
    for seat in seats:
        if seat.status is ConnectionStatus.needs_reauth:
            continue
        seat.status = ConnectionStatus.needs_reauth
        await session.flush()
        # The seat owner sees this in their notification feed; the audit trail is what tells an
        # admin *when* a channel went quiet, which is otherwise invisible after the fact.
        await audit.record(
            session,
            org_id=seat.organization_id,
            workspace_id=None,
            actor_user_id=seat.user_id,
            action="connection.needs_reauth",
            summary=f"{seat.provider.value} seat disconnected at the provider",
            target_type="connection",
            target_id=seat.id,
        )
    return "account_updated"


# --- Backfill: catching what the webhook never delivered ----------------------
#
# Webhooks are the fast path, not a guarantee. A subscription that lapsed, a deployment whose URL
# moved, an outage on either side — any of them loses a candidate's reply permanently, because
# nothing else ever asks the provider what arrived. This asks.
#
# It re-reads a window rather than tracking a watermark: overlapping a webhook costs nothing,
# because `provider_message_id` dedupes at record time, and a lookback is self-healing where a
# stored cursor can itself get stuck.

SWEEP_LOOKBACK_HOURS = 24


async def sweep_inbound(session: AsyncSession, *, now: datetime) -> dict[str, int]:
    """Pull recent messages for every healthy seat and record anything the webhook missed."""
    conn = unipile_connection()
    if conn is None:
        return {"swept": 0, "recovered": 0, "unreadable": 0}
    since = now - timedelta(hours=SWEEP_LOOKBACK_HOURS)
    seats = (
        (
            await session.execute(
                select(Connection).where(
                    Connection.external_id.is_not(None),
                    Connection.status == ConnectionStatus.ok,
                )
            )
        )
        .scalars()
        .all()
    )
    swept = recovered = unreadable = 0
    for seat in seats:
        account_id = seat.external_id
        if not account_id:
            continue
        # One seat's failure must not abort the sweep. The sweep shares the worker's session, so
        # an exception escaping here used to unwind the whole loop iteration — including the rows
        # recording messages that had already gone out on the wire.
        try:
            items = await conn.list_messages(account_id=account_id, since=since)
            if items is None:
                # Not "nothing arrived" — we could not tell. Loud, because a sweep that quietly
                # reads nothing looks exactly like a sweep that found nothing to recover.
                unreadable += 1
                logger.warning("inbound sweep: could not read messages for account %s", account_id)
                continue
            swept += 1
            for item in items:
                payload = dict(item)
                payload.setdefault("account_id", account_id)
                if await record_provider_event(session, payload=payload, now=now) == "queued":
                    recovered += 1
        except Exception:
            unreadable += 1
            logger.exception("inbound sweep: failed for account %s", account_id)
    if recovered:
        logger.info("inbound sweep: recovered %s message(s) the webhook never delivered", recovered)
    return {"swept": swept, "recovered": recovered, "unreadable": unreadable}
