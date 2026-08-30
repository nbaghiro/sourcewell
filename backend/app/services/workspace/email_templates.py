"""Transactional email templates, in the Sourcewell palette.

Email clients are a decade behind browsers: everything here is table-based with inline styles,
no SVG (Gmail strips it) and no CSS variables. The colours mirror `frontend/src/index.css` —
warm sand canvas, deep emerald rail, emerald primary — so account mail looks like the product.
Every template returns an HTML part *and* a plain-text part; multipart/alternative is what keeps
these out of spam.
"""

from dataclasses import dataclass
from html import escape

# --- palette (mirrors the app tokens) ----------------------------------------
SAND = "#f3f1ea"  # --background
INK = "#122019"  # --foreground
CARD = "#ffffff"  # --card
MUTED = "#6b7a71"  # --muted-foreground
BORDER = "#e5e1d5"  # --border
PRIMARY = "#0b5d4e"  # --primary
RAIL = "#07312b"  # --sidebar
RAIL_TEXT = "#ebf7f1"  # --sidebar-active-foreground
RAIL_MUTED = "#8fb6ac"  # --sidebar-foreground
SCORE_TO = "#43b68f"  # --score-to
FONT = (
    "'Hanken Grotesk', -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
)


def _hours(count: int) -> str:
    """A whole-hour duration in words, rolling up past two days so a week-long invite link reads
    as "7 days" rather than "168 hours". 24 stays hours — that is how a one-day link is spoken."""
    if count >= 48 and count % 24 == 0:
        return f"{count // 24} days"
    return f"{count} hour" + ("" if count == 1 else "s")


def _minutes(total: int) -> str:
    """A whole-minute duration in words: 45 -> "45 minutes", 60 -> "1 hour", 90 -> "1 hour 30
    minutes". The old form read `hours == 0 or minutes`, which threw the hours away whenever there
    were also minutes — 90 rendered as "30 minutes", understating the link's life by an hour."""
    hours, minutes = divmod(total, 60)
    if not hours:
        return f"{minutes} minutes"
    return _hours(hours) if not minutes else f"{_hours(hours)} {minutes} minutes"


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


def _shell(*, preheader: str, body: str) -> str:
    """The card + brand rail every transactional email sits in."""
    return f"""\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>Sourcewell</title>
</head>
<body style="margin:0; padding:0; background-color:{SAND}; -webkit-font-smoothing:antialiased;">
<div style="display:none; max-height:0; overflow:hidden; opacity:0;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:{SAND}; padding:32px 16px;">
  <tr>
    <td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
             style="width:600px; max-width:100%; background-color:{CARD}; border:1px solid {BORDER};
                    border-radius:14px; overflow:hidden;">
        <tr>
          <td style="background-color:{RAIL};
                     background-image:linear-gradient(120deg, {RAIL}, {PRIMARY} 78%,
                       {SCORE_TO} 160%);
                     padding:22px 28px;">
            <span style="font-family:{FONT}; font-size:19px; font-weight:700; letter-spacing:-0.2px;
                         color:{RAIL_TEXT};">Sourcewell</span>
          </td>
        </tr>
        <tr>
          <td style="padding:34px 28px 30px 28px; font-family:{FONT}; color:{INK};">
{body}
          </td>
        </tr>
      </table>
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
             style="width:600px; max-width:100%;">
        <tr>
          <td style="padding:18px 28px 0 28px; font-family:{FONT}; font-size:12px; line-height:19px;
                     color:{MUTED};" align="center">
            Sourcewell &middot; AI sourcing for recruiting teams
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


def _button(*, url: str, label: str) -> str:
    """A bulletproof (table-based) primary button — Outlook ignores padding on <a>."""
    return f"""\
            <table role="presentation" cellpadding="0" cellspacing="0" border="0"
                   style="margin:26px 0 22px 0;">
              <tr>
                <td align="center" bgcolor="{PRIMARY}" style="border-radius:10px;">
                  <a href="{escape(url, quote=True)}"
                     style="display:inline-block; padding:14px 30px; font-family:{FONT};
                            font-size:15px; font-weight:600; color:#f2fbf7;
                            text-decoration:none; border-radius:10px;">
                    {escape(label)}
                  </a>
                </td>
              </tr>
            </table>"""


def _link_email(
    *,
    subject: str,
    preheader: str,
    first_name: str,
    heading: str,
    lead_html: str,
    lead_text: str,
    button: str,
    url: str,
    expiry: str,
    footnote_html: str,
    footnote_text: str,
) -> RenderedEmail:
    """One account email: a greeting, a headline, a sentence, a button, the same link as text,
    when it expires, and what to do if it wasn't you. Both templates are this shape."""
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    body = f"""\
            <p style="margin:0 0 6px 0; font-size:15px; line-height:23px;
                      color:{MUTED};">{escape(greeting)}</p>
            <h1 style="margin:0 0 14px 0; font-size:25px; line-height:32px; font-weight:700;
                       letter-spacing:-0.4px; color:{INK};">{escape(heading)}</h1>
            <p style="margin:0; font-size:15px; line-height:24px; color:{INK};">
              {lead_html}
            </p>
{_button(url=url, label=button)}
            <p style="margin:0 0 8px 0; font-size:13px; line-height:20px; color:{MUTED};">
              Or paste this link into your browser:
            </p>
            <p style="margin:0 0 22px 0; font-size:13px; line-height:20px; word-break:break-all;">
              <a href="{escape(url, quote=True)}"
                 style="color:{PRIMARY}; text-decoration:underline;">{escape(url)}</a>
            </p>
            <p style="margin:0; font-size:13px; line-height:20px; color:{MUTED};">
              This link expires in {expiry}.
            </p>
            <hr style="border:none; border-top:1px solid {BORDER}; margin:26px 0 18px 0;">
            <p style="margin:0; font-size:12px; line-height:19px; color:{MUTED};">
              {footnote_html}
            </p>"""

    text = f"""\
{greeting}

{heading}

{lead_text}

{url}

This link expires in {expiry}.

{footnote_text}

Sourcewell · AI sourcing for recruiting teams"""

    return RenderedEmail(subject=subject, html=_shell(preheader=preheader, body=body), text=text)


def verification_email(*, first_name: str, url: str, ttl_hours: int) -> RenderedEmail:
    """The signup confirmation: one button, one fallback link, one expiry line."""
    return _link_email(
        subject="Confirm your email · Sourcewell",
        preheader="Confirm your email to activate your Sourcewell workspace.",
        first_name=first_name,
        heading="Confirm your email",
        lead_html=(
            "You're one click away from your Sourcewell workspace. Confirm this address and "
            "we'll sign you straight in."
        ),
        lead_text=(
            "You're one click away from your Sourcewell workspace. Confirm this address and\n"
            "we'll sign you straight in:"
        ),
        button="Confirm email address",
        url=url,
        expiry=_hours(ttl_hours),
        footnote_html=(
            "You're receiving this because this address was used to sign up for Sourcewell. "
            "If that wasn't you, you can safely ignore this email &mdash; no account is active "
            "until it's confirmed."
        ),
        footnote_text=(
            "You're receiving this because this address was used to sign up for Sourcewell.\n"
            "If that wasn't you, you can safely ignore this email \u2014 no account is active\n"
            "until it's confirmed."
        ),
    )


def password_reset_email(
    *, first_name: str, url: str, ttl_minutes: int, first_time: bool = False
) -> RenderedEmail:
    """The password link. Deliberately says nothing about the account beyond "someone asked".

    `first_time` is an account that has no password yet — an invited teammate, or someone who has
    only signed in with Google. Telling them to "reset" a password they never had reads as though
    someone else set one.
    """
    if first_time:
        return _link_email(
            subject="Set your Sourcewell password",
            preheader="Choose a password for your Sourcewell account.",
            first_name=first_name,
            heading="Set your password",
            lead_html=(
                "Someone asked to set a password for this Sourcewell account, so you can sign in "
                "with an email address as well as a provider. Choose one here &mdash; the link "
                "works once."
            ),
            lead_text=(
                "Someone asked to set a password for this Sourcewell account, so you can sign\n"
                "in with an email address as well as a provider. Choose one here (the link\n"
                "works once):"
            ),
            button="Choose a password",
            url=url,
            expiry=_minutes(ttl_minutes),
            footnote_html=(
                "Didn't ask for this? You can ignore this email &mdash; your account is unchanged, "
                "and no password is set until this link is used."
            ),
            footnote_text=(
                "Didn't ask for this? You can ignore this email \u2014 your account is unchanged,\n"
                "and no password is set until this link is used."
            ),
        )
    return _link_email(
        subject="Reset your Sourcewell password",
        preheader="Choose a new password for your Sourcewell account.",
        first_name=first_name,
        heading="Reset your password",
        lead_html=(
            "Someone asked to reset the password for this Sourcewell account. Choose a new one "
            "here &mdash; the link works once."
        ),
        lead_text=(
            "Someone asked to reset the password for this Sourcewell account. Choose a new\n"
            "one here (the link works once):"
        ),
        button="Choose a new password",
        url=url,
        expiry=_minutes(ttl_minutes),
        footnote_html=(
            "Didn't ask for this? You can ignore this email &mdash; your password stays as it is, "
            "and nobody can sign in without it."
        ),
        footnote_text=(
            "Didn't ask for this? You can ignore this email \u2014 your password stays as it is,\n"
            "and nobody can sign in without it."
        ),
    )


def invite_email(
    *, first_name: str, inviter: str, org_name: str, url: str, ttl_hours: int
) -> RenderedEmail:
    """The link that turns a pending invite into a real account.

    Clicking it is what proves the recipient controls this mailbox, so it carries the same weight
    as the signup confirmation: until it is clicked the invited row can't be signed in to and
    can't absorb an OAuth identity.
    """
    who = f"{inviter} " if inviter else ""
    return _link_email(
        subject=f"{who}invited you to {org_name} on Sourcewell".strip(),
        preheader=f"Join {org_name} on Sourcewell.",
        first_name=first_name,
        heading=f"Join {org_name}",
        lead_html=(
            f"{escape(inviter)} invited you to the <strong>{escape(org_name)}</strong> workspace "
            "on Sourcewell. Accept below and you'll be signed straight in."
            if inviter
            else (
                f"You've been invited to the <strong>{escape(org_name)}</strong> workspace on "
                "Sourcewell. Accept below and you'll be signed straight in."
            )
        ),
        lead_text=(
            f"{inviter} invited you to the {org_name} workspace on Sourcewell.\n"
            "Accept below and you'll be signed straight in:"
            if inviter
            else (
                f"You've been invited to the {org_name} workspace on Sourcewell.\n"
                "Accept below and you'll be signed straight in:"
            )
        ),
        button="Accept the invitation",
        url=url,
        expiry=_hours(ttl_hours),
        footnote_html=(
            "Weren't expecting this? You can ignore this email &mdash; the invitation does nothing "
            "until it's accepted, and nobody can sign in as you without it."
        ),
        footnote_text=(
            "Weren't expecting this? You can ignore this email \u2014 the invitation does nothing\n"
            "until it's accepted, and nobody can sign in as you without it."
        ),
    )
