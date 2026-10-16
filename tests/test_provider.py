import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

pytest.importorskip("stripe")
import stripe  # noqa: E402
from orbit_payments import PaymentRequest, PaymentWebhookError, RefundRequest  # noqa: E402

from orbit_payments_stripe import StripeConfig, StripePaymentProvider  # noqa: E402


def config() -> StripeConfig:
    return StripeConfig(api_key="sk_test_value", webhook_secret="whsec_value")


def test_webhook_timestamp_tolerance_cannot_disable_replay_check() -> None:
    with pytest.raises(ValidationError):
        StripeConfig(
            api_key="sk_test_value",
            webhook_secret="whsec_value",
            webhook_tolerance_seconds=0,
        )


class FakeStripeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.v1 = SimpleNamespace(
            payment_intents=SimpleNamespace(
                create_async=self.create_payment,
                retrieve_async=self.retrieve_payment,
            ),
            refunds=SimpleNamespace(create_async=self.create_refund),
        )

    async def create_payment(self, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("payment_intents.create", (payload, kwargs)))
        return {
            "id": "pi_123",
            "amount": payload["amount"],
            "currency": payload["currency"],
            "status": "requires_payment_method",
            "metadata": payload["metadata"],
            "client_secret": "pi_123_secret_token",
            "created": 1_780_000_000,
        }

    async def retrieve_payment(self, payment_id: str) -> dict[str, Any]:
        self.calls.append(("payment_intents.retrieve", (payment_id,)))
        return {
            "id": payment_id,
            "amount": 100,
            "currency": "usd",
            "status": "succeeded",
            "metadata": {"orbit_reference": "order-1"},
        }

    async def create_refund(self, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("refunds.create", (payload, kwargs)))
        return {
            "id": "re_123",
            "amount": payload["amount"],
            "currency": "usd",
            "status": "succeeded",
        }


@pytest.mark.asyncio
async def test_create_retrieve_refund_and_idempotency_keys() -> None:
    client = FakeStripeClient()
    provider = StripePaymentProvider(config(), client=client)
    payment = await provider.create_payment(
        PaymentRequest(amount_minor=100, currency="USD", reference="order-1")
    )
    retrieved = await provider.get_payment("pi_123")
    refund = await provider.refund(
        RefundRequest(payment_id="pi_123", reference="refund-1", amount_minor=50)
    )

    assert payment.status.value == "requires_action"
    assert payment.client_token == "pi_123_secret_token"
    assert retrieved.status.value == "succeeded"
    assert refund.provider_id == "re_123"
    assert client.calls[0][1][1]["options"]["idempotency_key"] == "order-1"
    assert client.calls[2][1][1]["options"]["idempotency_key"] == "refund-1"
    await provider.aclose()


def test_webhook_maps_only_verified_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    event = {
        "id": "evt_123",
        "type": "payment_intent.succeeded",
        "created": 1_780_000_000,
        "data": {"object": {"object": "payment_intent", "id": "pi_123"}},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *args, **kwargs: event)
    provider = StripePaymentProvider(config(), client=FakeStripeClient())
    verified = provider.verify_webhook(b"{}", "t=1,v1=signature")
    assert verified.event_id == "evt_123"
    assert verified.payment_id == "pi_123"

    def reject(*args: Any, **kwargs: Any) -> None:
        raise stripe.SignatureVerificationError("invalid", "signature")

    monkeypatch.setattr(stripe.Webhook, "construct_event", reject)
    with pytest.raises(PaymentWebhookError):
        provider.verify_webhook(b"{}", "bad")


@pytest.mark.asyncio
async def test_provider_requires_startup_without_injected_client() -> None:
    provider = StripePaymentProvider(config())
    with pytest.raises(RuntimeError):
        await provider.get_payment("pi_123")
    await provider.aclose()


@pytest.mark.asyncio
async def test_shutdown_drains_inflight_calls_before_closing_owned_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeHTTPClient:
        closed = False

        async def close_async(self) -> None:
            self.closed = True

    class BlockingStripeClient(FakeStripeClient):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def retrieve_payment(self, payment_id: str) -> dict[str, Any]:
            self.started.set()
            await self.release.wait()
            return {
                "id": payment_id,
                "amount": 100,
                "currency": "usd",
                "status": "succeeded",
                "metadata": {"orbit_reference": "order-1"},
            }

    http_client = FakeHTTPClient()
    client = BlockingStripeClient()
    monkeypatch.setattr(stripe, "HTTPXClient", lambda **_: http_client)
    monkeypatch.setattr(stripe, "StripeClient", lambda *_, **__: client)

    provider = StripePaymentProvider(config())
    await provider.startup()
    operation = asyncio.create_task(provider.get_payment("pi_123"))
    await asyncio.wait_for(client.started.wait(), timeout=1)

    shutdown = asyncio.create_task(provider.aclose())
    await asyncio.sleep(0)
    assert not shutdown.done()
    assert not http_client.closed
    with pytest.raises(RuntimeError, match="closed"):
        await provider.get_payment("pi_456")

    shutdown.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    assert not http_client.closed

    client.release.set()
    await operation
    await provider.aclose()
    assert http_client.closed


@pytest.mark.asyncio
async def test_failed_http_transport_close_can_be_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed async transport close keeps the owned client handle for a later attempt."""

    class FailOnceHTTPClient:
        def __init__(self) -> None:
            self.close_calls = 0
            self.closed = False

        async def close_async(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise RuntimeError("temporary close failure")
            self.closed = True

    http_client = FailOnceHTTPClient()
    monkeypatch.setattr(stripe, "HTTPXClient", lambda **_: http_client)
    monkeypatch.setattr(stripe, "StripeClient", lambda *_, **__: FakeStripeClient())
    provider = StripePaymentProvider(config())
    await provider.startup()

    with pytest.raises(RuntimeError, match="could not be closed"):
        await provider.aclose()
    with pytest.raises(RuntimeError, match="closed"):
        await provider.get_payment("pi_123")

    await provider.aclose()
    await provider.aclose()
    assert http_client.closed
    assert http_client.close_calls == 2
