"""Application settings (env-driven; defaults match docker-compose on the 89xx band)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # --- WorkOS AuthKit (SSO: Google / Microsoft / email) ---
    workos_api_key: str = ""
    workos_client_id: str = ""
    workos_redirect_uri: str = "http://localhost:8901/auth/callback"

    # --- Session ---
    # Both WorkOS SSO and LinkedIn (Unipile hosted-auth) sign-in mint the SAME sealed session: a
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

    # --- People-data providers (Rail B: licensed search/enrich APIs) ---
    # Platform-key mode. Leave blank to fall back to the synthetic demo provider.
    pdl_api_key: str = ""
    apollo_api_key: str = ""
    hunter_api_key: str = ""
    people_providers_demo: bool = True  # include the synthetic demo provider as a fallback

    # --- LinkedIn / multichannel send (Unipile) ---
    # Blank = LinkedIn sends are a no-op (dry-run), so multichannel sequences still complete in QA.
    unipile_api_key: str = ""
    unipile_dsn: str = ""  # e.g. https://apiXX.unipile.com:14XXX
    unipile_account_id: str = ""  # the connected LinkedIn account in Unipile
    # Shared secret embedded in the registered webhook URL (?token=) / X-Unipile-Token header; blank
    # disables the inbound Unipile receiver.
    unipile_webhook_secret: str = ""

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
        """WorkOS SSO (Google / Microsoft / email) is available."""
        return bool(self.workos_api_key and self.workos_client_id and self.session_cookie_password)

    @property
    def linkedin_auth_enabled(self) -> bool:
        """Sign in with LinkedIn (via Unipile hosted-auth) is available."""
        return bool(self.unipile_api_key and self.unipile_dsn and self.session_cookie_password)

    @property
    def auth_enabled(self) -> bool:
        """At least one real sign-in provider is configured; otherwise dev-header auth is used."""
        return self.workos_enabled or self.linkedin_auth_enabled

    @property
    def header_auth_enabled(self) -> bool:
        """X-User-Id header auth — only when no provider is configured (tests / no-SSO)."""
        return not self.auth_enabled


@lru_cache
def get_settings() -> Settings:
    return Settings()
