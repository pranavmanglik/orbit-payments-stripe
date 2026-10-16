# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Stripe PaymentIntent adapter using Stripe's first-party asynchronous Python SDK."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import stripe
from orbit_payments import (
    PaymentOperationError,
    PaymentRequest,
    PaymentSession,
    PaymentStatus,
    PaymentWebhookError,
    RefundRequest,
    RefundResult,
    RefundStatus,
    VerifiedWebhook,
)

from orbit_payments_stripe.config import StripeConfig


class StripePaymentProvider:
    """Create and manage Stripe PaymentIntents through StripeClient's async API."""

    def __init__(self, config: StripeConfig, *, client: Any | None = None) -> None:
        self._config = config
        self._client = client
        self._http_client: Any | None = None
        self._owns_client = client is None
        self._closed = False
        self._active_operations = 0
        self._lifecycle = asyncio.Condition()
        self._close_task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        """Create the Stripe SDK client and async HTTP transport once."""
        async with self._lifecycle:
            if self._closed:
                raise RuntimeError("Stripe payment provider is closed.")
            if self._client is not None:
                return
            http_client: Any = stripe.HTTPXClient(timeout=self._config.timeout_seconds)
            try:
                client = stripe.StripeClient(
                    self._config.api_key.get_secret_value(),
                    http_client=http_client,
                    max_network_retries=self._config.max_network_retries,
                )
            except BaseException as startup_error:
                try:
                    await http_client.close_async()
                except BaseException as close_error:
                    startup_error.add_note(
                        "The Stripe HTTP transport also failed to close after startup failed: "
                        f"{type(close_error).__name__}."
                    )
                raise
            self._http_client = http_client
            self._client = client

    async def aclose(self) -> None:
        """Stop calls, drain operations, and retain the owned transport until close succeeds.

        Injected SDK clients remain caller-owned. The provider still waits for operations started
        through this adapter before returning so their owner can safely shut that client down.
        Cancellation of an individual shutdown waiter does not cancel shared cleanup.
        """
        task = self._close_task
        retry_failed_close = (
            task is not None and task.done() and (task.cancelled() or task.exception() is not None)
        )
        if task is None or retry_failed_close:
            self._closed = True
            task = asyncio.create_task(self._close_resources())
            self._close_task = task
        await asyncio.shield(task)

    async def _close_resources(self) -> None:
        """Drain registered calls before releasing the owned transport."""
        async with self._lifecycle:
            while self._active_operations:
                await self._lifecycle.wait()
            http_client = self._http_client if self._owns_client else None
        if http_client is not None:
            try:
                await http_client.close_async()
            except asyncio.CancelledError:
                raise
            except Exception:
                raise RuntimeError("Stripe HTTP transport could not be closed.") from None
            async with self._lifecycle:
                if self._http_client is http_client:
                    self._http_client = None
                    self._client = None

    async def __aenter__(self) -> StripePaymentProvider:
        await self.startup()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def create_payment(self, request: PaymentRequest) -> PaymentSession:
        """Create a PaymentIntent using the merchant reference as Stripe's idempotency key."""
        async with self._operation_client() as client:
            metadata = {**request.metadata, "orbit_reference": request.reference}
            try:
                result = await client.v1.payment_intents.create_async(
                    {
                        "amount": request.amount_minor,
                        "currency": request.currency.lower(),
                        "metadata": metadata,
                        "automatic_payment_methods": {"enabled": True},
                    },
                    options={"idempotency_key": request.reference},
                )
                return self._session(result)
            except Exception:
                raise PaymentOperationError("Stripe could not create the payment.") from None

    async def get_payment(self, provider_id: str) -> PaymentSession:
        """Retrieve a PaymentIntent by its Stripe identifier."""
        async with self._operation_client() as client:
            try:
                result = await client.v1.payment_intents.retrieve_async(provider_id)
                return self._session(result)
            except Exception:
                raise PaymentOperationError("Stripe could not retrieve the payment.") from None

    async def refund(self, request: RefundRequest) -> RefundResult:
        """Create a full or partial PaymentIntent refund."""
        async with self._operation_client() as client:
            payload: dict[str, object] = {"payment_intent": request.payment_id}
            if request.amount_minor is not None:
                payload["amount"] = request.amount_minor
            try:
                result = await client.v1.refunds.create_async(
                    payload,
                    options={"idempotency_key": request.reference},
                )
                status = self._refund_status(str(result.get("status", "pending")))
                return RefundResult(
                    provider="stripe",
                    provider_id=str(result["id"]),
                    payment_id=request.payment_id,
                    amount_minor=int(result["amount"]),
                    currency=str(result["currency"]).upper(),
                    status=status,
                )
            except (KeyError, TypeError, ValueError):
                raise PaymentOperationError("Stripe returned an invalid refund response.") from None
            except Exception:
                raise PaymentOperationError("Stripe could not create the refund.") from None

    def verify_webhook(self, raw_body: bytes, signature: str) -> VerifiedWebhook:
        """Verify Stripe's timestamped signature before extracting event metadata."""
        if not isinstance(raw_body, bytes) or len(raw_body) > 1_048_576:
            raise PaymentWebhookError("Stripe webhook body is invalid or too large.")
        if not isinstance(signature, str) or not signature or len(signature) > 4_096:
            raise PaymentWebhookError("Stripe webhook signature is missing or invalid.")
        try:
            event = stripe.Webhook.construct_event(
                raw_body,
                signature,
                self._config.webhook_secret.get_secret_value(),
                tolerance=self._config.webhook_tolerance_seconds,
            )
            data = event["data"]
            resource = data["object"]
            created = event.get("created")
            return VerifiedWebhook(
                provider="stripe",
                event_id=str(event["id"]),
                event_type=str(event["type"]),
                payment_id=(
                    str(resource["id"]) if resource.get("object") == "payment_intent" else None
                ),
                occurred_at=(datetime.fromtimestamp(int(created), tz=UTC) if created else None),
            )
        except stripe.SignatureVerificationError:
            raise PaymentWebhookError("Stripe webhook signature verification failed.") from None
        except Exception:
            raise PaymentWebhookError("Stripe webhook payload is invalid.") from None

    @asynccontextmanager
    async def _operation_client(self) -> AsyncIterator[Any]:
        """Reserve one active-operation slot before exposing the client to an SDK call."""
        async with self._lifecycle:
            client = self._require_client()
            self._active_operations += 1
        try:
            yield client
        finally:
            await asyncio.shield(self._release_operation())

    async def _release_operation(self) -> None:
        """Release an operation slot even when the caller is cancelled."""
        async with self._lifecycle:
            self._active_operations -= 1
            if self._active_operations == 0:
                self._lifecycle.notify_all()

    def _require_client(self) -> Any:
        if self._closed:
            raise RuntimeError("Stripe payment provider is closed.")
        if self._client is None:
            raise RuntimeError("Start StripePaymentProvider before making provider calls.")
        return self._client

    @staticmethod
    def _session(value: Any) -> PaymentSession:
        status = str(value.get("status", "unknown"))
        normalized = {
            "requires_payment_method": PaymentStatus.REQUIRES_ACTION,
            "requires_confirmation": PaymentStatus.REQUIRES_ACTION,
            "requires_action": PaymentStatus.REQUIRES_ACTION,
            "processing": PaymentStatus.PROCESSING,
            "succeeded": PaymentStatus.SUCCEEDED,
            "canceled": PaymentStatus.CANCELLED,
        }.get(status, PaymentStatus.UNKNOWN)
        created = value.get("created")
        return PaymentSession(
            provider="stripe",
            provider_id=str(value["id"]),
            provider_resource="payment_intent",
            reference=(
                str(value.get("metadata", {}).get("orbit_reference"))
                if value.get("metadata", {}).get("orbit_reference") is not None
                else None
            ),
            amount_minor=int(value["amount"]),
            currency=str(value["currency"]).upper(),
            status=normalized,
            client_token=(str(value["client_secret"]) if value.get("client_secret") else None),
            created_at=(datetime.fromtimestamp(int(created), tz=UTC) if created else None),
        )

    @staticmethod
    def _refund_status(value: str) -> RefundStatus:
        return {
            "succeeded": RefundStatus.SUCCEEDED,
            "failed": RefundStatus.FAILED,
            "pending": RefundStatus.PENDING,
            "requires_action": RefundStatus.PENDING,
        }.get(value, RefundStatus.UNKNOWN)


__all__ = ["StripePaymentProvider"]
