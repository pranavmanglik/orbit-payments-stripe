# Orbit Payments Stripe

`orbit-payments-stripe` implements the `orbit-payments` contract with Stripe PaymentIntents and
Stripe's official asynchronous Python SDK.

```bash
python -m pip install orbit-payments-stripe
```

```python
from orbit_payments import PaymentRequest
from orbit_payments_stripe import StripeConfig, StripePaymentProvider

provider = StripePaymentProvider(
    StripeConfig.from_environment(),
)
await provider.startup()
try:
    session = await provider.create_payment(
        PaymentRequest(amount_minor=1999, currency="USD", reference="order-8742")
    )
    # Send the client secret only to the authorized customer flow.
finally:
    await provider.aclose()
```

The merchant reference is sent as Stripe's idempotency key and as `orbit_reference` metadata.
Retries for transient Stripe errors are delegated to the SDK, which reuses request idempotency.
Webhook verification uses Stripe's signed timestamp and configured tolerance against the raw body.
Use a webhook endpoint that preserves raw request bytes before JSON parsing.

The adapter does not create prices, subscriptions, customers, hosted Checkout Sessions, tax,
disputes, payouts, or settlement records. It does not accept or store card data. `client_token`
contains the PaymentIntent client secret and must be returned only through an authenticated,
authorized application flow. Do not log or persist it unnecessarily.

Set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` through a secret manager. No credentials are
provided by default. This is an alpha integration; run Stripe test-mode checks for each account,
API version, payment-method configuration, and webhook endpoint before deployment.

See [architecture](docs/architecture/overview.md), [operations](docs/operations/README.md),
[security](docs/security/overview.md), [development](docs/development/README.md), and the
[documentation index](docs/README.md).

Licensed under Apache-2.0.
