# Stripe operations

Set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` from a secret manager. Use separate Stripe
test-mode and live-mode secrets in the correct environment. The adapter configures finite SDK
timeouts, a bounded retry count, and an explicit asynchronous transport. Shutdown stops admitting
new provider calls and waits for in-flight calls before closing the owned transport. A failed or
cancelled transport close retains the handle for a later `aclose()` retry; the provider remains
unavailable after shutdown starts. The Stripe SDK provides request idempotency and retry behavior;
the adapter uses stable merchant references for create and refund requests.

Webhooks should be handled on an application-owned route that passes the original bytes and
`Stripe-Signature` header to `verify_webhook` before application parsing. The adapter does not
register a Core route, persist the event, publish a broker message, or run a consumer. The route
should durably store/enqueue the verified envelope before returning `2xx`; see the capability's
[durable webhook processing guide](https://github.com/orbit-projects/orbit-payments/blob/main/docs/operations/webhook-processing.md).
Deduplicate by scoped event ID and make handlers idempotent. Browser redirects are not payment
confirmation.

The unit suite uses fakes; no Stripe account, live endpoint, or test-mode sandbox acceptance is
claimed. Follow the official [Python SDK](https://github.com/stripe/stripe-python) and webhook docs
for provider account configuration and supported payment methods.
