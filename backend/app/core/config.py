"""Application settings (env-driven; defaults match docker-compose on the 89xx band)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# The Fernet key shipped in `.env.example` so a local run works with no setup. It is public — it
# lives in the repository — so a deployment that kept it has no session security whatsoever: every
# cookie is forgeable and every sealed provider secret is readable. Refused outside local below.
EXAMPLE_SESSION_COOKIE_PASSWORD = "cUYdTpasVSgR9RKRn_d2uAB5o-1ZkQ9CSPgjbPXVX6A="


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "sourcewell"
    environment: str = "local"
    database_url: str = "postgresql+asyncpg://sourcewell:sourcewell@localhost:8902/sourcewell"
    test_database_url: str = (
        "postgresql+asyncpg://sourcewell:sourcewell@localhost:8902/sourcewell_test"
    )
    smtp_host: str = "localhost"
    smtp_port: int = 8905
    default_from_email: str = "recruiter@sourcewell.dev"

    # Where the React app is served — used for CORS + post-auth redirects.
    frontend_url: str = "http://localhost:8900"

    # --- WorkOS (brokers the Google / Microsoft OAuth buttons) ---
    workos_api_key: str = ""
    workos_client_id: str = ""
    workos_redirect_uri: str = "http://localhost:8901/auth/callback"

    # --- Session ---
    # Google/Microsoft OAuth and email/password sign-in mint the SAME sealed session: a
    # Fernet-encrypted cookie holding the local user id. Generate the key with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    session_cookie_password: str = ""
    # Previous key kept during rotation so existing sealed secrets/cookies still decrypt.
    session_cookie_password_previous: str = ""
    session_cookie_name: str = "sw_session"
    cookie_secure: bool = False  # set True behind HTTPS
    # "none" (with cookie_secure=true) lets the session cookie ride cross-site — needed when the
    # frontend (localhost) talks to a backend served through an HTTPS tunnel (ngrok/cloudflared).
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # --- Abuse limits on the unauthenticated auth endpoints ---
    # Off in the test suite (which signs up dozens of times); on everywhere else.
    auth_rate_limits_enabled: bool = True
    auth_signup_per_hour: int = 10
    auth_login_per_5min: int = 20
    auth_mail_per_hour: int = 5  # forgot-password + resend-verification, per IP
    auth_email_cooldown_seconds: int = 60  # per address, whoever asks

    # --- LinkedIn (Unipile hosted-auth) seat connect ---
    # How long a pending connect attempt stays valid (matched to the wizard link's own 1h expiry).
    login_attempt_ttl_minutes: int = 60

    # --- Email/password sign-in ---
    # Consecutive failures before the account is locked, and for how long. The lock is per
    # account (the global middleware limiter is per IP; an attacker rotating IPs still hits this).
    login_max_attempts: int = 8
    login_lockout_minutes: int = 15
    password_reset_ttl_minutes: int = 60

    # --- Transactional email (verification + account mail) ---
    # Key-gated: blank = plain SMTP (Mailpit locally, so signup stays testable offline). Resend
    # delivers over HTTPS, so hosts that block outbound SMTP still send.
    resend_api_key: str = ""
    transactional_from_email: str = "Sourcewell <hello@sourcewell.dev>"
    email_verification_ttl_hours: int = 24
    # Longer than a signup confirmation on purpose: an invite arrives unprompted, so it has to
    # survive a weekend and a holiday rather than assuming the recipient was waiting for it.
    invite_ttl_hours: int = 24 * 7

    # --- AI (Anthropic Claude) ---
    # Blank = deterministic fallback everywhere; set to enable real generation/scoring.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # --- Agent runtime provider (backend-only; never exposed to users) ---
    # Which provider the agent runtime uses. One model, no tiers (yet). Blank model = a
    # per-provider default (see core/providers.py). Each provider has its own key.
    agent_provider: str = "anthropic"  # anthropic | openai | gemini | xai
    agent_model: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    xai_api_key: str = ""

    # --- Billing (Stripe) ---
    # Key-gated: blank = billing disabled (free tier only, no checkout). Stripe hosts all payment
    # entry (Checkout + Portal); we never see card data. Prices are the Stripe Price IDs per plan.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_pro: str = ""
    stripe_price_premium: str = ""

    # --- People-data providers (Rail B: licensed search/enrich APIs) ---
    # Platform-key mode; orgs can also bring their own key (ProviderCredential). With no key
    # configured anywhere, people search returns no results.
    pdl_api_key: str = ""
    apollo_api_key: str = ""
    hunter_api_key: str = ""

    # --- LinkedIn / multichannel send (Unipile) ---
    # Blank = LinkedIn sends are a no-op (dry-run), so multichannel sequences still complete in QA.
    # Never a sign-in path: LinkedIn is connected from Settings as a sending seat.
    unipile_api_key: str = ""
    unipile_dsn: str = ""  # e.g. https://apiXX.unipile.com:14XXX
    unipile_account_id: str = ""  # the connected LinkedIn account in Unipile
    # Shared secret embedded in the registered webhook URL (?token=) / X-Unipile-Token header; blank
    # disables the inbound Unipile receiver.
    unipile_webhook_secret: str = ""
    # Simulate sends instead of transmitting — a send becomes a no-op marked "sent". On for offline
    # dev / demo / CI; can be flipped on in staging to avoid messaging real people. (Read from the
    # EMAIL_DRY_RUN / LINKEDIN_DRY_RUN env vars.)
    email_dry_run: bool = False
    linkedin_dry_run: bool = False

    # --- Signing + public links ---
    # HMAC key for unsubscribe links + inbound webhook verification (falls back to the cookie key).
    signing_secret: str = ""
    # Absolute base URL of this API (used to build unsubscribe links in outbound email).
    api_base_url: str = "http://localhost:8901"
    # Shared secret a provider HMAC-signs inbound webhook bodies with (blank disables the check).
    inbound_webhook_secret: str = ""

    # --- Demo email/password login ---
    # The seeded demo account; its password is hashed at rest on the user (scrypt).
    demo_admin_email: str = "demo@sourcewell.ai"
    demo_password: str = "testpass"

    @property
    def workos_enabled(self) -> bool:
        """The Google / Microsoft OAuth buttons are available."""
        return bool(self.workos_api_key and self.workos_client_id and self.session_cookie_password)

    @property
    def seat_connect_enabled(self) -> bool:
        """Connecting a LinkedIn sending seat (via Unipile hosted-auth) is available.

        The webhook secret counts: the wizard hands the connected account to us over the
        server-side notify hop, and that endpoint is disabled without it — so without the secret
        every connect would leave the wizard with nowhere to report back to.
        """
        return bool(
            self.unipile_api_key
            and self.unipile_dsn
            and self.unipile_webhook_secret
            and self.session_cookie_password
        )

    @property
    def auth_enabled(self) -> bool:
        """A real sign-in provider is configured; otherwise dev-header auth is used.

        LinkedIn does not count — it is a sending seat, not a way in — so a deployment with only
        Unipile configured still has no sign-in provider and falls back to header auth
        in local/test.
        """
        return self.workos_enabled

    @property
    def is_local(self) -> bool:
        return self.environment.lower() in {"local", "test"}

    @property
    def header_auth_enabled(self) -> bool:
        """X-User-Id header auth — a local-only convenience for tests and the QA guide.

        Gated on the environment, never on which providers happen to be configured: a
        password-only production deployment has no OAuth provider either, and trusting a caller-
        supplied user id there would let anyone sign in as anyone.
        """
        return self.is_local and not self.auth_enabled

    def cookie_scope_warning(self) -> str | None:
        """Flag a config where the session cookie can't reach the app that needs it.

        The emailed links — verification, invite — and the OAuth callback all set the session
        cookie on whatever host the *browser* used to reach the API, which is `API_BASE_URL`, and
        then redirect to `FRONTEND_URL`. A cookie carries no `Domain`, so it is host-only: point
        those two at different sites and the user clicks "confirm", gets signed in on one host,
        lands on another, and is asked to log in again. Nothing errors — the sign-in silently
        didn't stick.

        A warning rather than a startup refusal: a tunnelled API with a localhost frontend is a
        legitimate local setup for testing provider webhooks, it just can't complete a cookie
        flow. Site comparison is the last two labels, which is right for the shapes that matter
        (`api.acme.com` / `app.acme.com` is one site) and imprecise for multi-part public
        suffixes — acceptable for a hint that costs nothing to ignore.
        """
        from urllib.parse import urlparse

        def site(url: str) -> str:
            host = (urlparse(url).hostname or "").lower()
            return ".".join(host.rsplit(".", 2)[-2:]) if host.count(".") >= 1 else host

        api, app = site(self.api_base_url), site(self.frontend_url)
        if not api or not app or api == app:
            return None
        # Explicitly configured for cross-site: the cookie is allowed to ride along.
        if self.cookie_samesite == "none" and self.cookie_secure:
            return None
        return (
            f"API_BASE_URL ({api}) and FRONTEND_URL ({app}) are different sites, so the session "
            "cookie set by an emailed verification/invite link or the OAuth callback will not be "
            "sent by the browser to the app — the user will appear signed out right after "
            "confirming. Point both at the same host, or set COOKIE_SAMESITE=none with "
            "COOKIE_SECURE=true."
        )

    def production_config_errors(self) -> list[str]:
        """Security settings that must not keep their development defaults outside local.

        Checked at startup — each of these is a full compromise on its own, and every one of them
        fails silently (the app works fine, it just isn't secure).
        """
        if self.is_local:
            return []
        problems = []
        generate = (
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
        if not self.session_cookie_password:
            problems.append(
                "SESSION_COOKIE_PASSWORD is unset: session cookies would be unencrypted "
                f"plaintext user ids, and anyone could forge one. Generate with: {generate}"
            )
        elif self.session_cookie_password == EXAMPLE_SESSION_COOKIE_PASSWORD:
            problems.append(
                "SESSION_COOKIE_PASSWORD is still the key from .env.example, which is public in "
                "the repository: every session cookie would be forgeable and every sealed "
                f"provider secret readable. Generate your own with: {generate}"
            )
        if not (self.signing_secret or self.session_cookie_password):
            problems.append(
                "SIGNING_SECRET is unset: email-verification and password-reset links would be "
                "signed with the public development fallback key, so anyone could forge them."
            )
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE is false: the session cookie would ride plain HTTP.")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            problems.append("COOKIE_SAMESITE=none requires COOKIE_SECURE=true.")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
