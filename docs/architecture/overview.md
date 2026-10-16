# Stripe adapter architecture

This package implements `orbit_payments.PaymentProvider` using Stripe's first-party `StripeClient`
and asynchronous HTTPX-backed SDK interface. `orbit-payments` owns the shared protocol and models;
this adapter owns the Stripe API, API key, webhook secret, retry configuration, and transport
lifecycle.

## Implemented operations

- `create_payment`: creates a PaymentIntent with integer minor-unit amount, lower-case currency,
  automatic payment methods, metadata, and the stable merchant reference as an idempotency key.
- `get_payment`: retrieves a PaymentIntent by provider ID.
- `refund`: creates a full or partial refund with the merchant refund reference as idempotency key.
- `verify_webhook`: validates Stripe's raw-body signature and timestamp using the configured
  tolerance before returning normalized event metadata.

Stripe statuses are mapped to the shared status enum. Provider-specific and unrecognized states
remain `unknown`; the adapter does not infer settlement or order fulfillment from an HTTP success.
The API version is the version selected by the installed Stripe SDK unless an application passes
per-request options through a separately supported API.

## Resource ownership

Call `startup()` before network methods and `aclose()` during shutdown, or use the provider as an
async context manager. Shutdown rejects new network calls, drains operations already admitted by
this provider, then closes its owned HTTPX transport. Cancelling a caller waiting for shutdown does
not cancel transport cleanup; a later `aclose()` waits on that same cleanup task. Injected SDK
clients remain caller-owned, and the provider still drains its active calls before returning from
shutdown. The package uses no module-global Stripe API key.
