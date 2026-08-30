"""Inbound webhook registration — the step that makes the receiver actually hear anything.

Unipile pushes events only to URLs that have been subscribed, and the subscription is per
deployment (and per dev tunnel), so it has to be asserted on boot. These tests pin that it is
idempotent, that it degrades toward over-registering rather than under-registering, and that it
can never take down a boot or a sign-in.
"""

import httpx
import pytest
import respx

from app.core.config import Settings
from app.ext import unipile as unipile_ext
from app.services.outreach import receiving

_DSN = "https://api7.unipile.com:7777"
_EXPECTED_URL = "https://api.sourcewell.dev/webhooks/unipile?token=shh"


def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        unipile_api_key="key",
        unipile_dsn=_DSN,
        unipile_webhook_secret="shh",
        api_base_url="https://api.sourcewell.dev",
    )
    monkeypatch.setattr(receiving, "get_settings", lambda: settings)
    monkeypatch.setattr(unipile_ext, "get_settings", lambda: settings)


# --- the receiver URL --------------------------------------------------------


async def test_receiver_url_is_none_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(receiving, "get_settings", lambda: Settings())
    assert receiving.receiver_url() is None


async def test_receiver_url_carries_the_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)
    assert receiving.receiver_url() == _EXPECTED_URL


async def test_unconfigured_registration_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(receiving, "get_settings", lambda: Settings())
    assert set((await receiving.ensure_inbound_webhooks()).values()) == {"skipped"}


# --- registration ------------------------------------------------------------


@respx.mock
async def test_subscribes_every_event_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """LinkedIn DMs, mailbox replies, and seat credential events each need a subscription."""
    _configured(monkeypatch)
    respx.get(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(200, json={"items": []}))
    created = respx.post(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(201, json={}))

    results = await receiving.ensure_inbound_webhooks()

    assert results == {"messaging": "registered", "email": "registered", "account": "registered"}
    subscribed = {call.request.url for call in created.calls}
    assert len(subscribed) == 1  # all three point at the one receiver
    assert created.call_count == 3


@respx.mock
async def test_existing_subscriptions_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running on every boot must not pile up duplicate subscriptions."""
    _configured(monkeypatch)
    respx.get(f"{_DSN}/api/v1/webhooks").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"request_url": _EXPECTED_URL, "source": "messaging"},
                    {"request_url": _EXPECTED_URL, "source": "account"},
                ]
            },
        )
    )
    created = respx.post(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(201, json={}))

    results = await receiving.ensure_inbound_webhooks()

    assert results["messaging"] == "present" and results["account"] == "present"
    assert results["email"] == "registered"
    assert created.call_count == 1  # only the missing one


@respx.mock
async def test_a_subscription_for_another_deployment_does_not_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Staging's URL sitting in the list must not convince us production is subscribed."""
    _configured(monkeypatch)
    respx.get(f"{_DSN}/api/v1/webhooks").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "request_url": "https://staging.example/webhooks/unipile?token=x",
                        "source": "messaging",
                    }
                ]
            },
        )
    )
    created = respx.post(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(201, json={}))

    results = await receiving.ensure_inbound_webhooks()

    assert results["messaging"] == "registered"
    assert created.call_count == 3


@respx.mock
async def test_an_unreadable_list_registers_blindly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failing open: a duplicate delivery is deduped downstream, a missing one is silence."""
    _configured(monkeypatch)
    respx.get(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(500))
    created = respx.post(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(201, json={}))

    results = await receiving.ensure_inbound_webhooks()

    assert set(results.values()) == {"registered"}
    assert created.call_count == 3


@respx.mock
async def test_a_rejected_subscription_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configured(monkeypatch)
    respx.get(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(200, json={"items": []}))
    respx.post(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(422, json={}))

    assert set((await receiving.ensure_inbound_webhooks()).values()) == {"failed"}


@respx.mock
async def test_a_dead_provider_never_breaks_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """This runs inside app startup and the sign-in notify — it must not be able to break either."""
    _configured(monkeypatch)
    respx.get(f"{_DSN}/api/v1/webhooks").mock(side_effect=httpx.ConnectError("down"))
    respx.post(f"{_DSN}/api/v1/webhooks").mock(side_effect=httpx.ConnectError("down"))

    await receiving.ensure_inbound_webhooks_quietly()  # no raise
