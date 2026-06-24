"""RBAC guards + signing/HMAC + unsubscribe token (Phase 5 hardening)."""

import hashlib
import hmac

import pytest
from fastapi import HTTPException

from app.core import signing
from app.deps import TenantContext, require_org_admin, require_workspace
from app.people import suppression


def _ctx(*, is_admin: bool, workspace: str | None = "w") -> TenantContext:
    return TenantContext(
        user_id="u",
        org_id="o",
        roles=frozenset(),
        is_org_admin=is_admin,
        allowed_workspace_ids=frozenset({"w"}),
        current_workspace_id=workspace,
    )


def test_require_org_admin_blocks_non_admin() -> None:
    with pytest.raises(HTTPException) as exc:
        require_org_admin(_ctx(is_admin=False))
    assert exc.value.status_code == 403
    require_org_admin(_ctx(is_admin=True))  # admin: no raise


def test_require_workspace_needs_header() -> None:
    assert require_workspace(_ctx(is_admin=True, workspace="w")) == "w"
    with pytest.raises(HTTPException) as exc:
        require_workspace(_ctx(is_admin=True, workspace=None))
    assert exc.value.status_code == 400


def test_signing_roundtrip_and_tamper() -> None:
    token = signing.sign("org_1|a@b.com")
    assert signing.verify(token) == "org_1|a@b.com"
    assert signing.verify(token + "x") is None
    assert signing.verify("garbage") is None


def test_unsubscribe_token_roundtrip() -> None:
    token = suppression.unsubscribe_token("org_1", "A@B.com")
    assert suppression.parse_unsubscribe(token) == ("org_1", "a@b.com")
    assert suppression.parse_unsubscribe("tampered.token") is None


def test_hmac_verify() -> None:
    body = b'{"from":"x@y.com","text":"hi"}'
    sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert signing.verify_hmac(body, sig, secret="secret")
    assert signing.verify_hmac(body, f"sha256={sig}", secret="secret")
    assert not signing.verify_hmac(body, "deadbeef", secret="secret")
    assert not signing.verify_hmac(body, None, secret="secret")
