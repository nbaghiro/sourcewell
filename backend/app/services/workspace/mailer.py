"""Transactional delivery: Resend when a key is set, plain SMTP (Mailpit) otherwise.

Kept separate from `services/outreach/messaging.py` — that one sends *campaign* mail from a
recruiter's connected seat and carries unsubscribe headers; this sends *account* mail from the
platform. EMAIL_DRY_RUN=1 skips delivery entirely (the test suite sets it).
"""

import asyncio
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings
from app.ext.resend import resend_mailer
from app.services.workspace.email_templates import RenderedEmail


def _send_smtp_sync(host: str, port: int, sender: str, to: str, mail: RenderedEmail) -> None:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = mail.subject
    msg.set_content(mail.text)
    msg.add_alternative(mail.html, subtype="html")
    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.send_message(msg)


async def send_transactional(*, to: str, mail: RenderedEmail) -> bool:
    """Deliver one account email. Returns False when delivery failed (never raises)."""
    s = get_settings()
    # Read through Settings, not os.getenv: pydantic accepts 1/true/yes/on for a bool, and the
    # raw == "1" check silently sent real mail for every other spelling of the same flag.
    if s.email_dry_run:
        return True
    mailer = resend_mailer()
    try:
        if mailer is not None:
            sent = await mailer.send(
                sender=s.transactional_from_email,
                to=to,
                subject=mail.subject,
                html=mail.html,
                text=mail.text,
            )
            return sent is not None
        await asyncio.to_thread(
            _send_smtp_sync, s.smtp_host, s.smtp_port, s.transactional_from_email, to, mail
        )
        return True
    except Exception:
        # A signup must not 500 because the mail hop is down — the caller offers a resend.
        return False
